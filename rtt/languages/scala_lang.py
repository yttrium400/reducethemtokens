SYMBOLS_QUERY = ""  # Scala extraction uses direct AST traversal.
IMPORTS_QUERY = ""


def declaration_signature(source: bytes, node) -> str:
    """Return a compact Scala declaration without its implementation body."""
    end_byte = node.end_byte
    for child in node.children:
        if child.type in ("block", "template_body"):
            end_byte = child.start_byte
            break
        # For single-expression bodies like `def f(x: Int): Int = x + 1`
        # the = sign precedes the expression, so strip from = onwards
        if child.type == "=":
            end_byte = child.start_byte
            break
    signature = source[node.start_byte:end_byte].decode(errors="replace").strip()
    return " ".join(signature.split())


def extract_fn_signature(source: bytes, name_node, params_node=None, return_node=None) -> str:
    name = source[name_node.start_byte:name_node.end_byte].decode()
    params = (
        source[params_node.start_byte:params_node.end_byte].decode()
        if params_node
        else "()"
    )
    ret = ""
    if return_node:
        ret = ": " + source[return_node.start_byte:return_node.end_byte].decode()
    return f"def {name}{params}{ret}"


def extract_class_signature(source: bytes, node) -> str:
    return declaration_signature(source, node)
