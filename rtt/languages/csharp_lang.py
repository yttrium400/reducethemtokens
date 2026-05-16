SYMBOLS_QUERY = """
(class_declaration
  name: (identifier) @class_name) @class

(method_declaration
  type: (_) @return_type
  name: (identifier) @method_name
  parameters: (parameter_list) @method_params) @method

(interface_declaration
  name: (identifier) @interface_name) @interface

(struct_declaration
  name: (identifier) @struct_name) @struct

(enum_declaration
  name: (identifier) @enum_name) @enum

(property_declaration
  type: (_) @property_type
  name: (identifier) @property_name) @property
"""

IMPORTS_QUERY = """
(using_directive
  (qualified_name) @import_path)
"""


def extract_fn_signature(source: bytes, name_node, params_node, return_node=None) -> str:
    name = source[name_node.start_byte:name_node.end_byte].decode()
    params = source[params_node.start_byte:params_node.end_byte].decode()
    ret = source[return_node.start_byte:return_node.end_byte].decode() if return_node else "void"
    return f"{ret} {name}{params}"


def extract_class_signature(source: bytes, node) -> str:
    name_node = node.child_by_field_name("name")
    if not name_node:
        return ""
    name = source[name_node.start_byte:name_node.end_byte].decode()
    kind = "class"
    for child in node.children:
        if child.type in ("class", "struct", "interface", "enum"):
            kind = child.type
            break
    return f"{kind} {name}"
