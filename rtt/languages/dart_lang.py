SYMBOLS_QUERY = ""  # Dart extraction uses direct AST traversal.
IMPORTS_QUERY = ""


def _text(source: bytes, node) -> str:
    return source[node.start_byte:node.end_byte].decode(errors="replace")


def declaration_signature(source: bytes, node) -> str:
    """Return a compact Dart declaration without its implementation body."""
    end_byte = node.end_byte
    for child in node.children:
        if child.type in ("class_body", "enum_body", "extension_body"):
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
        ret = source[return_node.start_byte:return_node.end_byte].decode() + " "
    return f"{ret}{name}{params}"


def extract_class_signature(source: bytes, node) -> str:
    return declaration_signature(source, node)
