import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from tree_sitter import Node, Parser

from rtt import FileIndex, Symbol
from rtt.extractor import SKIP_DIRS, _extract_file
from rtt.languages.registry import detect_language, get_ts_language

# Which node types count as symbols per language (ground truth)
SYMBOL_NODE_TYPES: dict[str, dict[str, str]] = {
    "python": {
        "function_definition": "function",
        "class_definition": "class",
    },
    "javascript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
    },
    "typescript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "interface_declaration": "interface",
    },
    "tsx": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "interface_declaration": "interface",
    },
    "go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_spec": "type",
    },
    "rust": {
        "function_item": "function",
        "struct_item": "struct",
        "enum_item": "enum",
        "trait_item": "trait",
        "impl_item": "impl",
    },
    "java": {
        "class_declaration": "class",
        "method_declaration": "method",
        "interface_declaration": "interface",
        "constructor_declaration": "constructor",
    },
    "kotlin": {
        "function_declaration": "function",
        "class_declaration": "class",
        "object_declaration": "object",
    },
    "c": {
        "function_definition": "function",
        "struct_specifier": "struct",
    },
    "cpp": {
        "function_definition": "function",
        "struct_specifier": "struct",
        "class_specifier": "class",
    },
    "ruby": {
        "method": "method",
        "class": "class",
        "module": "module",
    },
    "swift": {
        "function_declaration": "function",
        "protocol_function_declaration": "function",
        "class_declaration": "type",
        "protocol_declaration": "protocol",
    },
    "csharp": {
        "class_declaration": "class",
        "method_declaration": "method",
        "interface_declaration": "interface",
        "struct_declaration": "struct",
        "enum_declaration": "enum",
        "property_declaration": "property",
    },
    "lua": {
        "function_declaration": "function",
        "local_function": "function",
    },
    "dart": {
        "class_definition": "class",
        "function_signature": "function",
        "method_signature": "function",
        "mixin_declaration": "mixin",
        "enum_declaration": "enum",
        "extension_declaration": "extension",
    },
    "php": {
        "class_declaration": "class",
        "method_declaration": "function",
        "function_definition": "function",
        "interface_declaration": "interface",
        "trait_declaration": "trait",
        "enum_declaration": "enum",
    },
}

# Name field for each node type (tree-sitter field names)
NAME_FIELDS = ["name", "declarator"]

# Stop recursing into these nodes - their bodies contain nested implementation details,
# not public symbols. Mirrors rtt's own extraction boundary.
_FUNCTION_BODY_TYPES: dict[str, frozenset[str]] = {
    "python":     frozenset({"function_definition"}),
    "javascript": frozenset({"function_declaration", "function_expression", "arrow_function", "method_definition"}),
    "typescript": frozenset({"function_declaration", "function_expression", "arrow_function", "method_definition"}),
    "tsx":        frozenset({"function_declaration", "function_expression", "arrow_function", "method_definition"}),
    "go":         frozenset({"function_declaration", "method_declaration", "func_literal"}),
    "rust":       frozenset({"function_item"}),
    "java":       frozenset({"method_declaration", "constructor_declaration"}),
    "kotlin":     frozenset({"function_declaration"}),
    "c":          frozenset({"function_definition"}),
    "cpp":        frozenset({"function_definition"}),
    "ruby":       frozenset({"method", "singleton_method"}),
    "swift":      frozenset({
        "function_declaration",
        "protocol_function_declaration",
    }),
    "csharp":     frozenset({"method_declaration", "constructor_declaration"}),
    "lua":        frozenset({"function_declaration", "local_function"}),
    "dart":       frozenset({"function_body", "block"}),
    "php":        frozenset({"method_declaration", "function_definition"}),
}


@dataclass
class GroundTruthSymbol:
    name: str
    kind: str
    line: int  # 1-based


@dataclass
class SignatureIssue:
    symbol_name: str
    kind: str
    signature: str
    issue: str  # human-readable description


@dataclass
class FileAudit:
    path: str
    language: str
    expected: int
    found: int
    missing: list[GroundTruthSymbol] = field(default_factory=list)
    signature_issues: list[SignatureIssue] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return self.found / self.expected * 100 if self.expected > 0 else 100.0

    @property
    def passed(self) -> bool:
        return not self.missing and not self.signature_issues


@dataclass
class AuditReport:
    files: list[FileAudit] = field(default_factory=list)

    @property
    def total_expected(self) -> int:
        return sum(f.expected for f in self.files)

    @property
    def total_found(self) -> int:
        return sum(f.found for f in self.files)

    @property
    def coverage(self) -> float:
        return self.total_found / self.total_expected * 100 if self.total_expected > 0 else 100.0

    @property
    def total_signature_issues(self) -> int:
        return sum(len(f.signature_issues) for f in self.files)

    @property
    def files_with_issues(self) -> list[FileAudit]:
        return [f for f in self.files if not f.passed]


def _extract_name(node: Node, source: bytes) -> Optional[str]:
    """Best-effort name extraction from a symbol node."""
    for field_name in NAME_FIELDS:
        child = node.child_by_field_name(field_name)
        if child:
            # For function_declarator (C/C++), drill one level deeper
            if child.type == "function_declarator":
                inner = child.child_by_field_name("declarator")
                if inner:
                    return source[inner.start_byte:inner.end_byte].decode(errors="replace").strip()
            raw = source[child.start_byte:child.end_byte].decode(errors="replace").strip()
            # Take only the first token (handles cases like `Type Name`)
            return raw.split()[0] if raw else None

    # Fallback: first named child that looks like an identifier
    for child in node.children:
        if child.type in ("identifier", "type_identifier", "field_identifier",
                          "property_identifier", "constant"):
            return source[child.start_byte:child.end_byte].decode(errors="replace").strip()

    return None


def _walk_ground_truth(node: Node, source: bytes, lang: str,
                       results: list[GroundTruthSymbol],
                       seen: set[str],
                       in_fn_body: bool = False):
    """Recursively walk the full AST to collect symbol nodes.

    Stops recursion at function bodies so nested helper functions (which rtt
    intentionally ignores) are not counted as missing symbols.
    """
    if in_fn_body:
        return

    type_map = SYMBOL_NODE_TYPES.get(lang, {})
    kind = type_map.get(node.type)

    if kind:
        name = _extract_name(node, source)
        if name and name not in seen:
            seen.add(name)
            results.append(GroundTruthSymbol(
                name=name,
                kind=kind,
                line=node.start_point[0] + 1,
            ))

    fn_body_types = _FUNCTION_BODY_TYPES.get(lang, frozenset())

    # For decorated_definition, recurse into the wrapped node (not the decorator itself).
    if node.type == "decorated_definition":
        for child in node.children:
            _walk_ground_truth(child, source, lang, results, seen, in_fn_body=False)
        return

    # Mark children as inside-a-function-body when the current node is a function.
    next_in_fn = node.type in fn_body_types

    for child in node.children:
        _walk_ground_truth(child, source, lang, results, seen, in_fn_body=next_in_fn)


def _flatten_rtt_symbols(symbols: list[Symbol], out: Optional[list] = None) -> list[Symbol]:
    """Flatten nested rtt symbol tree into a flat list."""
    if out is None:
        out = []
    for sym in symbols:
        out.append(sym)
        _flatten_rtt_symbols(sym.children, out)
    return out


def _check_signature(sym: Symbol, source: bytes, lang: str) -> Optional[SignatureIssue]:
    """Check a single signature for obvious accuracy problems."""
    sig = sym.signature.strip()

    if not sig:
        return SignatureIssue(sym.name, sym.kind, sig, "signature is empty")

    if sym.name not in sig:
        return SignatureIssue(sym.name, sym.kind, sig,
                              f"symbol name '{sym.name}' not found in signature")

    is_callable = sym.kind in ("function", "method", "constructor")
    if is_callable:
        if "(" not in sig or ")" not in sig:
            return SignatureIssue(sym.name, sym.kind, sig,
                                  "parentheses missing - parameters not captured")

        open_idx = sig.index("(")
        close_idx = sig.rindex(")")
        if close_idx < open_idx:
            return SignatureIssue(sym.name, sym.kind, sig,
                                  "malformed parentheses in signature")

        # Check for truncation: signature ends mid-token (no closing bracket at all)
        if sig.count("(") != sig.count(")"):
            return SignatureIssue(sym.name, sym.kind, sig,
                                  "unbalanced parentheses - likely truncated")

    # Check that return type annotation is present when source has one
    if lang == "python" and "->" in source.decode(errors="replace"):
        # Only flag if this specific function's source line has `->`
        # (we don't have per-symbol source here, so just check sig length heuristic)
        pass

    if len(sig) < 4:
        return SignatureIssue(sym.name, sym.kind, sig,
                              "signature suspiciously short")

    return None


def audit_file(filepath: str) -> Optional[FileAudit]:
    lang = detect_language(filepath)
    if not lang:
        return None

    ts_lang = get_ts_language(lang)
    if not ts_lang:
        return None

    source = Path(filepath).read_bytes()
    parser = Parser(ts_lang)
    tree = parser.parse(source)

    # Ground truth: full AST walk (stops at function bodies to match rtt's scope)
    ground_truth: list[GroundTruthSymbol] = []
    _walk_ground_truth(tree.root_node, source, lang, ground_truth, seen=set())

    # rtt extraction
    file_index = _extract_file(filepath)
    rtt_symbols = _flatten_rtt_symbols(file_index.symbols) if file_index else []
    rtt_names = {s.name for s in rtt_symbols}

    # Coverage: which ground truth symbols did rtt miss?
    missing = [gt for gt in ground_truth if gt.name not in rtt_names]

    # Signature accuracy: check every symbol rtt did find
    sig_issues: list[SignatureIssue] = []
    for sym in rtt_symbols:
        issue = _check_signature(sym, source, lang)
        if issue:
            sig_issues.append(issue)

    rel_path = filepath
    return FileAudit(
        path=rel_path,
        language=lang,
        expected=len(ground_truth),
        found=len(ground_truth) - len(missing),
        missing=missing,
        signature_issues=sig_issues,
    )


def audit_repo(path: str) -> AuditReport:
    root = Path(path).resolve()
    report = AuditReport()

    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            result = audit_file(filepath)
            if result and result.expected > 0:
                result.path = os.path.relpath(filepath, str(root))
                report.files.append(result)

    report.files.sort(key=lambda f: f.coverage)
    return report
