import hashlib
import os
from pathlib import Path
from typing import Optional

from tree_sitter import Parser, Node

from rtt import FileIndex, RepoIndex, Symbol, CompareReport
from rtt.languages.registry import detect_language, get_ts_language
from rtt.languages import LANG_MODULES
from rtt.tokenizer import count_tokens
from rtt.cache import Cache

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".pytest_cache",
    "venv", ".venv", "env", ".env", "dist", "build", ".next", ".nuxt",
    "target", ".rtt-cache", ".idea", ".vscode", "coverage", ".nyc_output",
}


def _get_docstring(node: Node, source: bytes) -> Optional[str]:
    """Extract first string literal from a function body as docstring."""
    for child in node.children:
        if child.type == "block":
            for stmt in child.children:
                if stmt.type == "expression_statement":
                    for expr in stmt.children:
                        if expr.type in ("string", "string_literal"):
                            raw = source[expr.start_byte:expr.end_byte].decode(errors="replace")
                            cleaned = raw.strip("'\"` \n").split("\n")[0].strip()
                            if cleaned:
                                return cleaned[:80]
    return None


def _extract_file(filepath: str, cache: Optional[Cache] = None) -> Optional[FileIndex]:
    path = Path(filepath)
    if not path.is_file():
        return None

    lang_name = detect_language(filepath)
    if not lang_name:
        return None

    ts_lang = get_ts_language(lang_name)
    if not ts_lang:
        return None

    source = path.read_bytes()
    file_hash = hashlib.sha256(source).hexdigest()

    if cache:
        cached = cache.get(filepath, file_hash)
        if cached:
            return cached

    lang_mod = LANG_MODULES.get(lang_name)
    if not lang_mod:
        return None

    parser = Parser(ts_lang)
    tree = parser.parse(source)

    symbols = _extract_symbols(tree.root_node, source, lang_name, lang_mod)
    imports = _extract_imports(tree.root_node, source, lang_name, lang_mod)

    file_index = FileIndex(
        path=filepath,
        language=lang_name,
        imports=imports,
        symbols=symbols,
    )

    if cache:
        cache.set(filepath, file_hash, file_index)

    return file_index


def _extract_imports(root: Node, source: bytes, lang_name: str, _lang_mod) -> list[str]:
    """Extract import names using direct AST traversal (no query API needed).

    For bare module imports (import os, import 'fs'), stores just the module name.
    For named imports (from pathlib import Path, import { X } from 'y'), stores
    module.Symbol entries so the skeleton captures which specific symbols are used.
    """
    seen: set[str] = set()
    result: list[str] = []

    def add(name: str):
        name = name.strip().strip("\"'` ")
        if name and name not in seen:
            seen.add(name)
            result.append(name)

    def add_module(name: str):
        """Add a bare module name (strip path separators and clean up)."""
        name = name.strip().strip("\"'` ")
        # Take only the root package (e.g. "os.path" → "os", '"fs"' → "fs")
        name = name.split(".")[0].split("/")[-1]
        add(name)

    def text(node: Node) -> str:
        return source[node.start_byte:node.end_byte].decode(errors="replace")

    def add_named_imports(mod_name: str, names_node: Node):
        """For 'from mod import X, Y' - emit mod.X, mod.Y entries."""
        for child in names_node.children:
            if child.type in ("dotted_name", "identifier"):
                sym = text(child).strip()
                if sym and sym != "*":
                    add(f"{mod_name}.{sym}")
            elif child.type == "aliased_import":
                n = child.child_by_field_name("name")
                if n:
                    add(f"{mod_name}.{text(n).strip()}")
            elif child.type == "wildcard_import":
                add(f"{mod_name}.*")

    for node in root.children:
        t = node.type

        if lang_name == "python":
            if t == "import_statement":
                # import os  /  import os, sys  /  import os as o
                for child in node.children:
                    if child.type == "dotted_name":
                        add_module(text(child))
                    elif child.type == "aliased_import":
                        dn = child.child_by_field_name("name")
                        if dn:
                            add_module(text(dn))
            elif t == "import_from_statement":
                # from pathlib import Path  /  from . import x
                mod = node.child_by_field_name("module_name")
                mod_name = text(mod).strip() if mod else ""
                if not mod_name or mod_name.startswith("."):
                    # relative import - just store the module name if any
                    if mod_name.lstrip("."):
                        add_module(mod_name.lstrip("."))
                else:
                    # Collect named imports: from X import Y, Z
                    names_added = False
                    for child in node.children:
                        if child.type == "import_from_as_clause":
                            n = child.child_by_field_name("name")
                            if n:
                                add(f"{mod_name}.{text(n).strip()}")
                                names_added = True
                        elif child.type in ("dotted_name", "identifier") and child != mod:
                            sym = text(child).strip()
                            if sym and sym not in ("import",):
                                add(f"{mod_name}.{sym}")
                                names_added = True
                        elif child.type == "wildcard_import":
                            add(f"{mod_name}.*")
                            names_added = True
                    if not names_added and mod_name:
                        add_module(mod_name)

        elif lang_name in ("javascript", "typescript", "tsx"):
            if t == "import_statement":
                src = node.child_by_field_name("source")
                if not src:
                    continue
                raw_mod = text(src).strip().strip("\"'`")
                mod_name = raw_mod.split("/")[-1]
                # Look for named imports: import { X, Y } from '...'
                named_added = False
                for child in node.children:
                    if child.type == "import_clause":
                        for sub in child.children:
                            if sub.type == "named_imports":
                                for spec in sub.children:
                                    if spec.type == "import_specifier":
                                        n = spec.child_by_field_name("name")
                                        if n:
                                            add(f"{mod_name}.{text(n).strip()}")
                                            named_added = True
                            elif sub.type == "identifier":
                                # default import: import X from 'y'
                                add(mod_name)
                                named_added = True
                if not named_added:
                    add(mod_name)

        elif lang_name == "go":
            # import "fmt"  or  import_spec inside import_declaration
            if t == "import_declaration":
                for spec in node.children:
                    if spec.type == "import_spec":
                        p = spec.child_by_field_name("path")
                        if p:
                            add_module(text(p))
            elif t == "import_spec":
                p = node.child_by_field_name("path")
                if p:
                    add_module(text(p))

        elif lang_name == "rust":
            if t == "use_declaration":
                arg = node.child_by_field_name("argument")
                if arg:
                    full = text(arg).strip()
                    # use std::collections::HashMap → std::collections::HashMap
                    # use std::collections::{HashMap, BTreeMap} → keep as-is
                    add(full)

        elif lang_name == "java":
            if t == "import_declaration":
                for child in node.children:
                    if child.type == "scoped_identifier":
                        # java.util.HashMap → store full name
                        add(text(child).strip())
                        break

        elif lang_name == "kotlin":
            if t == "import":
                for child in node.children:
                    if child.type == "qualified_identifier":
                        add(text(child).strip())
                        break

        elif lang_name in ("c", "cpp"):
            if t == "preproc_include":
                for child in node.children:
                    if child.type in ("string_literal", "system_lib_string"):
                        add_module(text(child))

        elif lang_name == "ruby":
            if t == "call":
                method = node.child_by_field_name("method")
                if method and text(method) in ("require", "require_relative"):
                    args = node.child_by_field_name("arguments")
                    if args:
                        for child in args.children:
                            if child.type == "string":
                                add_module(text(child))

        elif lang_name == "swift":
            if t == "import_declaration":
                for child in node.children:
                    if child.type == "identifier":
                        add_module(text(child))
                        break

        elif lang_name == "csharp":
            if t == "using_directive":
                for child in node.children:
                    if child.type == "qualified_name":
                        add(text(child).strip())
                        break
                    elif child.type == "identifier":
                        add(text(child).strip())
                        break

        elif lang_name == "lua":
            def _extract_lua_require(fc_node):
                fn_id = None
                for ch in fc_node.children:
                    if ch.type == "identifier" and text(ch) == "require":
                        fn_id = ch
                        break
                if not fn_id:
                    return
                args = fc_node.child_by_field_name("arguments")
                if not args:
                    return
                for arg in args.children:
                    if arg.type == "string":
                        for sc in arg.children:
                            if sc.type == "string_content":
                                add_module(text(sc))
                                return

            if t == "variable_declaration":
                for child in node.children:
                    if child.type == "assignment_statement":
                        for sub in child.children:
                            if sub.type == "expression_list":
                                for expr in sub.children:
                                    if expr.type == "function_call":
                                        _extract_lua_require(expr)
            elif t == "function_call":
                _extract_lua_require(node)

        elif lang_name == "dart":
            if t == "import_or_export":
                def find_uri(n):
                    if n.type == "uri":
                        return n
                    for child in n.children:
                        result = find_uri(child)
                        if result:
                            return result
                    return None

                uri_node = find_uri(node)
                if uri_node:
                    uri_text = text(uri_node)
                    uri_text = uri_text.strip(chr(39) + chr(34))
                    for prefix in ("dart:", "package:"):
                        if uri_text.startswith(prefix):
                            uri_text = uri_text[len(prefix):]
                    if uri_text.endswith('.dart'):
                        uri_text = uri_text[:-5]
                    add_module(uri_text)

        elif lang_name == "scala":
            if t == "import_declaration":
                # import foo.bar.Baz or import foo.bar.{Baz, Qux}
                parts = []
                for child in node.children:
                    if child.type == "identifier":
                        parts.append(text(child))
                    elif child.type == "namespace_selectors":
                        # import foo.bar.{Baz, Qux} -> foo.bar
                        break
                if parts:
                    add(".".join(parts))

        if len(result) >= 30:
            break

    return result


# Python nodes that are transparent containers - look inside for top-level definitions.
# Handles patterns like: try: ... def f(): ... except: ... def f(): ...
_PY_TRANSPARENT = frozenset({
    "try_statement", "except_clause", "finally_clause",
    "with_statement", "if_statement", "else_clause", "elif_clause",
})


def _iter_js_iife_body(node: Node):
    """Yield statement_block children from a top-level IIFE expression.

    Handles patterns like:
        (function($) { function show() {} })(jQuery);
        !function() { function show() {} }();
    """
    # node is expression_statement - unwrap to the expression inside
    expr = None
    for child in node.children:
        if child.type == "call_expression":
            expr = child
            break
        if child.type == "unary_expression":
            for sub in child.children:
                if sub.type == "call_expression":
                    expr = sub
                    break
    if not expr:
        return

    fn_node = expr.child_by_field_name("function")
    if fn_node is None:
        return

    # Unwrap parenthesized_expression: (function() { ... })
    if fn_node.type == "parenthesized_expression":
        for child in fn_node.children:
            if child.type == "function_expression":
                fn_node = child
                break

    if fn_node.type != "function_expression":
        return

    body = fn_node.child_by_field_name("body")
    if body and body.type == "statement_block":
        yield from body.children


def _iter_toplevel_nodes(root: Node, lang_name: str):
    """Yield candidate top-level definition nodes, including transparent containers."""
    for child in root.children:
        yield child
        if lang_name == "python" and child.type in _PY_TRANSPARENT:
            yield from _iter_inside_transparent(child, depth=0)
        elif lang_name in ("javascript", "typescript", "tsx"):
            if child.type == "expression_statement":
                yield from _iter_js_iife_body(child)
            elif child.type in ("statement_block", "ERROR"):
                # Bare block statements { ... } and ERROR recovery nodes -
                # both used by Django-style JS that wraps code in a top-level block
                yield from child.children
        elif lang_name == "csharp" and child.type == "namespace_declaration":
            # C# namespaces wrap all declarations — look inside their declaration_list
            body = child.child_by_field_name("body")
            if body:
                yield from body.children


def _iter_inside_transparent(node: Node, depth: int):
    """Recursively yield definition nodes from inside transparent containers."""
    if depth > 4:
        return
    for child in node.children:
        if child.type == "block":
            for grandchild in child.children:
                yield grandchild
                if grandchild.type in _PY_TRANSPARENT:
                    yield from _iter_inside_transparent(grandchild, depth + 1)
        elif child.type in _PY_TRANSPARENT:
            yield from _iter_inside_transparent(child, depth + 1)


def _extract_symbols(root: Node, source: bytes, lang_name: str, lang_mod) -> list[Symbol]:
    """Walk the AST and extract top-level symbols."""
    symbols = []
    # Deduplicate by (name, kind) so that e.g. `struct Config` and `impl Config`
    # in Rust are both included, while try/except duplicate functions collapse to one.
    seen: set[tuple[str, str]] = set()

    for node in _iter_toplevel_nodes(root, lang_name):
        sym = _node_to_symbol(node, source, lang_name, lang_mod, depth=0)
        if sym and (sym.name, sym.kind) not in seen:
            symbols.append(sym)
            seen.add((sym.name, sym.kind))

    return symbols


def _node_to_symbol(node: Node, source: bytes, lang_name: str, lang_mod, depth: int) -> Optional[Symbol]:
    if depth > 2:
        return None

    sym = None

    try:
        if lang_name == "python":
            sym = _extract_python_symbol(node, source, lang_mod)
        elif lang_name in ("javascript", "typescript", "tsx"):
            sym = _extract_js_symbol(node, source, lang_mod, lang_name)
        elif lang_name == "go":
            sym = _extract_go_symbol(node, source, lang_mod)
        elif lang_name == "rust":
            sym = _extract_rust_symbol(node, source, lang_mod)
        elif lang_name == "java":
            sym = _extract_java_symbol(node, source, lang_mod)
        elif lang_name == "kotlin":
            sym = _extract_kotlin_symbol(node, source, lang_mod)
        elif lang_name in ("c", "cpp"):
            sym = _extract_c_symbol(node, source, lang_mod)
        elif lang_name == "ruby":
            sym = _extract_ruby_symbol(node, source, lang_mod)
        elif lang_name == "swift":
            sym = _extract_swift_symbol(node, source, lang_mod)
        elif lang_name == "csharp":
            sym = _extract_csharp_symbol(node, source, lang_mod)
        elif lang_name == "lua":
            sym = _extract_lua_symbol(node, source, lang_mod)
        elif lang_name == "dart":
            sym = _extract_dart_symbol(node, source, lang_mod)
        elif lang_name == "scala":
            sym = _extract_scala_symbol(node, source, lang_mod)
    except Exception:
        return None

    if (
        sym
        and sym.kind in ("class", "struct", "enum", "protocol", "extension", "interface", "object", "trait", "mixin", "case_class")
        and depth == 0
    ):
        # Unwrap wrapper nodes to reach the actual class definition node whose
        # "block" / "class_body" child holds the class body.
        # Handles: @dataclass class Foo  ->  decorated_definition -> class_definition
        #          export class Foo      ->  export_statement     -> class_declaration
        class_node = node
        _WRAPPER_TYPES = ("decorated_definition", "export_statement")
        _CLASS_TYPES   = ("class_definition", "class_declaration", "protocol_declaration", "object_declaration", "trait_declaration")
        if node.type in _WRAPPER_TYPES:
            for child in node.children:
                if child.type in _CLASS_TYPES:
                    class_node = child
                    break

        body = _find_child_by_type(
            class_node,
            [
                "block",
                "class_body",
                "enum_class_body",
                "protocol_body",
                "declaration_list",
                "body",
                "extension_body",
                "mixin_body",
                "template_body",
            ],
        )
        if body:
            for child in body.children:
                try:
                    child_sym = _node_to_symbol(child, source, lang_name, lang_mod, depth + 1)
                    if child_sym:
                        sym.children.append(child_sym)
                except Exception:
                    pass

    return sym


def _find_child_by_type(node: Node, types: list[str]) -> Optional[Node]:
    for child in node.children:
        if child.type in types:
            return child
    return None


def _find_child(node: Node, field: str) -> Optional[Node]:
    return node.child_by_field_name(field)


def _extract_python_symbol(node: Node, source: bytes, lang_mod) -> Optional[Symbol]:
    if node.type == "function_definition":
        name_node = node.child_by_field_name("name")
        params_node = node.child_by_field_name("parameters")
        return_node = node.child_by_field_name("return_type")
        if not name_node or not params_node:
            return None
        sig = lang_mod.extract_fn_signature(source, name_node, params_node, return_node)
        doc = _get_docstring(node, source)
        return Symbol(
            name=source[name_node.start_byte:name_node.end_byte].decode(),
            kind="function",
            signature=sig,
            docstring=doc,
        )

    if node.type == "class_definition":
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        args = node.child_by_field_name("superclasses")
        bases = ""
        if args:
            bases = source[args.start_byte:args.end_byte].decode()
        sig = f"class {name}{bases}"
        return Symbol(name=name, kind="class", signature=sig)

    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in ("function_definition", "class_definition"):
                return _extract_python_symbol(child, source, lang_mod)

    return None


def _extract_js_symbol(node: Node, source: bytes, lang_mod, lang_name: str) -> Optional[Symbol]:
    if node.type == "function_declaration":
        name_node = node.child_by_field_name("name")
        params_node = node.child_by_field_name("parameters")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        params = source[params_node.start_byte:params_node.end_byte].decode() if params_node else "()"
        return Symbol(name=name, kind="function", signature=f"function {name}{params}")

    if node.type == "class_declaration":
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        return Symbol(name=name, kind="class", signature=f"class {name}")

    if node.type == "method_definition":
        name_node = node.child_by_field_name("name")
        params_node = node.child_by_field_name("parameters")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        params = source[params_node.start_byte:params_node.end_byte].decode() if params_node else "()"
        return Symbol(name=name, kind="method", signature=f"{name}{params}")

    if node.type == "export_statement":
        for child in node.children:
            if child.type in ("function_declaration", "class_declaration"):
                return _extract_js_symbol(child, source, lang_mod, lang_name)

    if node.type in ("lexical_declaration", "variable_declaration"):
        keyword = "const" if node.type == "lexical_declaration" else "var"
        for decl in node.children:
            if decl.type == "variable_declarator":
                name_node = decl.child_by_field_name("name")
                value_node = decl.child_by_field_name("value")
                if not name_node or not value_node:
                    continue
                name = source[name_node.start_byte:name_node.end_byte].decode()
                if value_node.type == "arrow_function":
                    params_node = value_node.child_by_field_name("parameters") or value_node.child_by_field_name("parameter")
                    params = source[params_node.start_byte:params_node.end_byte].decode() if params_node else "()"
                    return Symbol(name=name, kind="function", signature=f"{keyword} {name} = {params} =>")
                if value_node.type == "function_expression":
                    params_node = value_node.child_by_field_name("parameters")
                    params = source[params_node.start_byte:params_node.end_byte].decode() if params_node else "()"
                    return Symbol(name=name, kind="function", signature=f"function {name}{params}")

    return None


def _extract_go_symbol(node: Node, source: bytes, lang_mod) -> Optional[Symbol]:
    if node.type == "function_declaration":
        name_node = node.child_by_field_name("name")
        params_node = node.child_by_field_name("parameters")
        result_node = node.child_by_field_name("result")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        params = source[params_node.start_byte:params_node.end_byte].decode() if params_node else "()"
        ret = " " + source[result_node.start_byte:result_node.end_byte].decode() if result_node else ""
        return Symbol(name=name, kind="function", signature=f"func {name}{params}{ret}")

    if node.type == "method_declaration":
        name_node = node.child_by_field_name("name")
        params_node = node.child_by_field_name("parameters")
        recv = node.child_by_field_name("receiver")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        params = source[params_node.start_byte:params_node.end_byte].decode() if params_node else "()"
        receiver = source[recv.start_byte:recv.end_byte].decode() if recv else ""
        return Symbol(name=name, kind="method", signature=f"func {receiver} {name}{params}")

    if node.type == "type_declaration":
        for spec in node.children:
            if spec.type == "type_spec":
                name_node = spec.child_by_field_name("name")
                type_node = spec.child_by_field_name("type")
                if name_node:
                    name = source[name_node.start_byte:name_node.end_byte].decode()
                    type_str = source[type_node.start_byte:type_node.end_byte].decode() if type_node else ""
                    kind = "struct" if "struct" in type_str else "type"
                    return Symbol(name=name, kind=kind, signature=f"type {name} {type_str[:40]}")

    return None


def _extract_rust_symbol(node: Node, source: bytes, lang_mod) -> Optional[Symbol]:
    if node.type == "function_item":
        name_node = node.child_by_field_name("name")
        params_node = node.child_by_field_name("parameters")
        return_node = node.child_by_field_name("return_type")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        params = source[params_node.start_byte:params_node.end_byte].decode() if params_node else "()"
        ret = " -> " + source[return_node.start_byte:return_node.end_byte].decode() if return_node else ""
        return Symbol(name=name, kind="function", signature=f"fn {name}{params}{ret}")

    if node.type in ("struct_item", "enum_item", "trait_item"):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        kind = node.type.replace("_item", "")
        return Symbol(name=name, kind=kind, signature=f"{kind} {name}")

    if node.type == "impl_item":
        type_node = node.child_by_field_name("type")
        if not type_node:
            return None
        type_name = source[type_node.start_byte:type_node.end_byte].decode()
        sym = Symbol(name=type_name, kind="impl", signature=f"impl {type_name}")
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                try:
                    child_sym = _extract_rust_symbol(child, source, lang_mod)
                    if child_sym:
                        sym.children.append(child_sym)
                except Exception:
                    pass
        return sym

    return None


def _extract_java_symbol(node: Node, source: bytes, lang_mod) -> Optional[Symbol]:
    if node.type == "class_declaration":
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        return Symbol(name=name, kind="class", signature=f"class {name}")

    if node.type == "method_declaration":
        name_node = node.child_by_field_name("name")
        params_node = node.child_by_field_name("parameters")
        type_node = node.child_by_field_name("type")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        params = source[params_node.start_byte:params_node.end_byte].decode() if params_node else "()"
        ret = source[type_node.start_byte:type_node.end_byte].decode() if type_node else "void"
        return Symbol(name=name, kind="method", signature=f"{ret} {name}{params}")

    if node.type == "interface_declaration":
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        return Symbol(name=name, kind="interface", signature=f"interface {name}")

    return None


def _kotlin_kind(node: Node) -> str:
    if node.type == "object_declaration":
        return "object"
    for child in node.children:
        if child.type in ("class", "interface"):
            return child.type
    return "class"


def _extract_kotlin_symbol(node: Node, source: bytes, lang_mod) -> Optional[Symbol]:
    if node.type == "function_declaration":
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        signature = lang_mod.declaration_signature(source, node)
        return Symbol(name=name, kind="function", signature=signature)

    if node.type in ("class_declaration", "object_declaration"):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        kind = _kotlin_kind(node)
        return Symbol(
            name=name,
            kind=kind,
            signature=lang_mod.extract_class_signature(source, node),
        )

    return None


def _extract_c_symbol(node: Node, source: bytes, lang_mod) -> Optional[Symbol]:
    if node.type == "function_definition":
        declarator = node.child_by_field_name("declarator")
        if not declarator:
            return None
        if declarator.type == "function_declarator":
            name_node = declarator.child_by_field_name("declarator")
            params_node = declarator.child_by_field_name("parameters")
            if not name_node:
                return None
            name = source[name_node.start_byte:name_node.end_byte].decode()
            params = source[params_node.start_byte:params_node.end_byte].decode() if params_node else "()"
            return Symbol(name=name, kind="function", signature=f"{name}{params}")

    if node.type in ("struct_specifier", "class_specifier"):
        name_node = node.child_by_field_name("name")
        if name_node:
            name = source[name_node.start_byte:name_node.end_byte].decode()
            kind = "struct" if node.type == "struct_specifier" else "class"
            return Symbol(name=name, kind=kind, signature=f"{kind} {name}")

    return None


def _extract_ruby_symbol(node: Node, source: bytes, lang_mod) -> Optional[Symbol]:
    if node.type == "method":
        name_node = node.child_by_field_name("name")
        params_node = node.child_by_field_name("parameters")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        params = source[params_node.start_byte:params_node.end_byte].decode() if params_node else ""
        return Symbol(name=name, kind="method", signature=f"def {name}{params}")

    if node.type == "class":
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        return Symbol(name=name, kind="class", signature=f"class {name}")

    if node.type == "module":
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        return Symbol(name=name, kind="module", signature=f"module {name}")

    return None


def _swift_keyword(node: Node) -> Optional[str]:
    for child in node.children:
        if child.type in ("class", "struct", "enum", "extension", "protocol"):
            return child.type
    return None


def _extract_csharp_symbol(node: Node, source: bytes, lang_mod) -> Optional[Symbol]:
    if node.type == "class_declaration":
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        return Symbol(name=name, kind="class", signature=f"class {name}")

    if node.type == "method_declaration":
        name_node = node.child_by_field_name("name")
        params_node = node.child_by_field_name("parameters")
        type_node = node.child_by_field_name("type")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        params = source[params_node.start_byte:params_node.end_byte].decode() if params_node else "()"
        ret = source[type_node.start_byte:type_node.end_byte].decode() if type_node else "void"
        return Symbol(name=name, kind="method", signature=f"{ret} {name}{params}")

    if node.type == "interface_declaration":
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        return Symbol(name=name, kind="interface", signature=f"interface {name}")

    if node.type == "struct_declaration":
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        return Symbol(name=name, kind="struct", signature=f"struct {name}")

    if node.type == "enum_declaration":
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        return Symbol(name=name, kind="enum", signature=f"enum {name}")

    if node.type == "property_declaration":
        name_node = node.child_by_field_name("name")
        type_node = node.child_by_field_name("type")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        prop_type = source[type_node.start_byte:type_node.end_byte].decode() if type_node else "object"
        return Symbol(name=name, kind="property", signature=f"{prop_type} {name}")

    return None


def _extract_swift_symbol(node: Node, source: bytes, lang_mod) -> Optional[Symbol]:
    if node.type in ("function_declaration", "protocol_function_declaration"):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        signature = lang_mod.declaration_signature(source, node)
        return Symbol(name=name, kind="function", signature=signature)

    if node.type == "protocol_declaration":
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        return Symbol(
            name=name,
            kind="protocol",
            signature=lang_mod.extract_class_signature(source, node),
        )

    if node.type == "class_declaration":
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        kind = _swift_keyword(node) or "class"
        return Symbol(
            name=name,
            kind=kind,
            signature=lang_mod.extract_class_signature(source, node),
        )

    return None


def _matches_any(rel_path: str, patterns: list[str]) -> bool:
    """Return True if rel_path matches any of the given glob patterns."""
    from pathlib import PurePosixPath
    p = PurePosixPath(rel_path.replace(os.sep, "/"))
    for pat in patterns:
        if p.match(pat):
            return True
    return False


def _trim_to_budget(
    files: list,
    max_tokens: int,
) -> tuple[list, int]:
    """Select files by priority until max_tokens budget is reached.

    Priority: non-test files first, then by symbol count descending.
    Returns (selected_files_sorted, n_dropped).
    """
    from rtt.formatter import format_file_text

    def _priority(fi) -> int:
        path = fi.path.lower().replace(os.sep, "/")
        is_test = any(m in path for m in _TEST_PATH_MARKERS)
        n_syms = sum(1 + len(s.children) for s in fi.symbols)
        return (0 if is_test else 10_000) + n_syms

    ordered = sorted(files, key=_priority, reverse=True)
    selected = []
    used = 0
    for fi in ordered:
        cost = count_tokens(format_file_text(fi))
        if used + cost <= max_tokens:
            selected.append(fi)
            used += cost

    dropped = len(files) - len(selected)
    return sorted(selected, key=lambda f: f.path), dropped


_TEST_PATH_MARKERS = ("test", "spec", "fixture", "mock", "__pycache__")


def _is_test_file(rel_path: str) -> bool:
    p = rel_path.lower().replace(os.sep, "/")
    return any(m in p for m in _TEST_PATH_MARKERS)


def extract_repo(
    path: str,
    use_cache: bool = True,
    include: Optional[list[str]] = None,
    exclude: Optional[list[str]] = None,
    max_tokens: Optional[int] = None,
    no_tests: bool = False,
) -> RepoIndex:
    """Extract a structural index of the repository.

    Args:
        path: Root directory to index.
        use_cache: Use per-file content-hash cache.
        include: Glob patterns - only files matching at least one are included.
                 e.g. ["src/**", "lib/**", "*.py"]
        exclude: Glob patterns - files matching any are excluded.
                 e.g. ["tests/**", "vendor/**"]
        max_tokens: If set, trim the result to fit within this token budget,
                    prioritising non-test files with the most symbols.
        no_tests: If True, exclude test/spec/fixture files entirely.
    """
    root = Path(path).resolve()
    cache = Cache(str(root)) if use_cache else None
    files = []

    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            file_index = _extract_file(filepath, cache)
            if file_index:
                rel = os.path.relpath(filepath, str(root))
                file_index.path = rel

                if include and not _matches_any(rel, include):
                    continue
                if exclude and _matches_any(rel, exclude):
                    continue
                if no_tests and _is_test_file(rel):
                    continue

                files.append(file_index)

    if cache:
        cache.save()

    files = sorted(files, key=lambda f: f.path)

    dropped = 0
    if max_tokens is not None:
        files, dropped = _trim_to_budget(files, max_tokens)

    repo = RepoIndex(files=files)
    repo._dropped = dropped  # carry forward for CLI reporting
    return repo


def compare_repo(path: str) -> CompareReport:
    from rtt.tokenizer import count_raw_repo_tokens
    from rtt.formatter import format_text, format_file_text

    root = Path(path).resolve()
    repo_index = extract_repo(str(root))

    raw_total, per_file_raw = count_raw_repo_tokens(str(root))
    compressed_total = count_tokens(format_text(repo_index))

    per_file = []
    for file_index in repo_index.files:
        compressed = count_tokens(format_file_text(file_index))
        raw = per_file_raw.get(file_index.path, 0)
        per_file.append({
            "path": file_index.path,
            "raw": raw,
            "compressed": compressed,
        })

    return CompareReport(
        path=str(root),
        raw_tokens=raw_total,
        compressed_tokens=compressed_total,
        file_count=len(repo_index.files),
        per_file=sorted(per_file, key=lambda x: x["raw"], reverse=True),
    )


def _extract_lua_symbol(node: Node, source: bytes, lang_mod) -> Optional[Symbol]:
    # function M.greet(name) or function M:add(a, b)
    if node.type == "function_declaration":
        is_local = any(child.type == "local" for child in node.children)
        name_node = None
        for child in node.children:
            if child.type in ("identifier", "dot_index_expression", "method_index_expression"):
                name_node = child
                break
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        params_node = node.child_by_field_name("parameters")
        params = source[params_node.start_byte:params_node.end_byte].decode() if params_node else "()"
        if is_local:
            return Symbol(name=name, kind="function", signature=f"local function {name}{params}")
        return Symbol(name=name, kind="function", signature=f"function {name}{params}")

    # local M = {} — treat as table/class
    if node.type == "variable_declaration":
        for child in node.children:
            if child.type == "assignment_statement":
                var_list = None
                expr_list = None
                for sub in child.children:
                    if sub.type == "variable_list":
                        var_list = sub
                    elif sub.type == "expression_list":
                        expr_list = sub
                if var_list and expr_list:
                    for var in var_list.children:
                        if var.type == "identifier":
                            for expr in expr_list.children:
                                if expr.type == "table_constructor":
                                    name = source[var.start_byte:var.end_byte].decode()
                                    return Symbol(name=name, kind="table", signature=f"local {name} = {{}}")
                                if expr.type == "anonymous_function":
                                    name = source[var.start_byte:var.end_byte].decode()
                                    params_node = expr.child_by_field_name("parameters")
                                    params = source[params_node.start_byte:params_node.end_byte].decode() if params_node else "()"
                                    return Symbol(name=name, kind="function", signature=f"local {name} = function{params}")
    return None


def _extract_dart_symbol(node: Node, source: bytes, lang_mod) -> Optional[Symbol]:
    """Extract a Dart symbol from a top-level node."""
    t = node.type

    # function_signature or method_signature (abstract methods)
    if t in ("function_signature", "method_signature"):
        name_node = node.child_by_field_name("name")
        if not name_node:
            # method_signature wraps function_signature
            for child in node.children:
                if child.type == "function_signature":
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        break
        if not name_node:
            for child in node.children:
                if child.type == "identifier":
                    name_node = child
                    break
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        sig = source[node.start_byte:node.end_byte].decode().strip()
        return Symbol(name=name, kind="function", signature=sig)

    # getter_signature (Dart getters like `String get name`)
    if t == "getter_signature":
        name_node = node.child_by_field_name("name")
        if not name_node:
            for child in node.children:
                if child.type == "identifier":
                    name_node = child
                    break
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        sig = source[node.start_byte:node.end_byte].decode().strip()
        return Symbol(name=name, kind="property", signature=sig)

    # declaration (abstract methods, constructors, class members)
    if t == "declaration":
        # For class members, the name is on the inner function_signature/getter_signature
        inner_kind = "function"
        name_node = node.child_by_field_name("name")
        if not name_node:
            for child in node.children:
                if child.type in ("function_signature", "getter_signature", "method_signature"):
                    if child.type == "getter_signature":
                        inner_kind = "property"
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        break
        if not name_node:
            for child in node.children:
                if child.type == "identifier":
                    name_node = child
                    break
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        end_byte = node.end_byte
        for child in node.children:
            if child.type in ("function_body", "block"):
                end_byte = child.start_byte
                break
        sig = source[node.start_byte:end_byte].decode().strip()
        return Symbol(name=name, kind=inner_kind, signature=sig)

    # class_definition, mixin_declaration, enum_declaration, extension_declaration
    if t in ("class_definition", "mixin_declaration", "enum_declaration", "extension_declaration"):
        name_node = node.child_by_field_name("name")
        if not name_node:
            for child in node.children:
                if child.type == "identifier":
                    name_node = child
                    break
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        end_byte = node.end_byte
        for child in node.children:
            if child.type in ("class_body", "enum_body", "extension_body", "mixin_body"):
                end_byte = child.start_byte
                break
        sig = source[node.start_byte:end_byte].decode().strip()
        sig = " ".join(sig.split())
        kind_map = {
            "class_definition": "class",
            "mixin_declaration": "mixin",
            "enum_declaration": "enum",
            "extension_declaration": "extension",
        }
        return Symbol(name=name, kind=kind_map.get(t, "class"), signature=sig)

    return None


def _extract_scala_symbol(node: Node, source: bytes, lang_mod) -> Optional[Symbol]:
    """Extract a Scala symbol from a top-level node."""
    t = node.type

    if t in ("function_definition", "function_declaration"):
        name_node = node.child_by_field_name("name")
        if not name_node:
            for child in node.children:
                if child.type == "identifier":
                    name_node = child
                    break
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        sig = lang_mod.declaration_signature(source, node)
        return Symbol(name=name, kind="function", signature=sig)

    if t in ("class_definition", "trait_definition", "object_definition"):
        name_node = node.child_by_field_name("name")
        if not name_node:
            for child in node.children:
                if child.type == "identifier":
                    name_node = child
                    break
        if not name_node:
            return None
        name = source[name_node.start_byte:name_node.end_byte].decode()
        sig = lang_mod.declaration_signature(source, node)
        kind_map = {
            "class_definition": "class",
            "trait_definition": "trait",
            "object_definition": "object",
        }
        if t == "class_definition":
            for child in node.children:
                if child.type == "case":
                    kind_map["class_definition"] = "case_class"
                    break
        return Symbol(name=name, kind=kind_map.get(t, "class"), signature=sig)

    return None
