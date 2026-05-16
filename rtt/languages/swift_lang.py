SYMBOLS_QUERY = ""  # Swift extraction uses direct AST traversal.
IMPORTS_QUERY = ""


def _text(source: bytes, node) -> str:
    return source[node.start_byte:node.end_byte].decode(errors="replace")


def declaration_signature(source: bytes, node) -> str:
    """Return a compact Swift declaration without its implementation body."""
    end_byte = node.end_byte
    for child in node.children:
        if child.type in (
            "function_body",
            "class_body",
            "enum_class_body",
            "protocol_body",
        ):
            end_byte = child.start_byte
            break
    signature = source[node.start_byte:end_byte].decode(errors="replace").strip()
    return " ".join(signature.rstrip("{").split())


def extract_fn_signature(source: bytes, name_node, params_node=None, return_node=None) -> str:
    name = source[name_node.start_byte:name_node.end_byte].decode()
    params = _text(source, params_node) if params_node else "()"
    ret = ""
    if return_node:
        ret = " -> " + _text(source, return_node)
    return f"func {name}{params}{ret}"


def extract_class_signature(source: bytes, node) -> str:
    return declaration_signature(source, node)
