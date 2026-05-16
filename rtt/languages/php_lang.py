SYMBOLS_QUERY = """
(function_definition
  name: (name) @fn_name
  parameters: (formal_parameters) @fn_params
  return_type: (_)? @fn_return) @function

(class_declaration
  name: (name) @class_name) @class

(method_declaration
  name: (name) @method_name
  parameters: (formal_parameters) @method_params
  return_type: (_)? @method_return) @method

(interface_declaration
  name: (name) @interface_name) @interface

(trait_declaration
  name: (name) @trait_name) @trait

(enum_declaration
  name: (name) @enum_name) @enum
"""

IMPORTS_QUERY = """
(namespace_use_declaration
  (namespace_use_clause
    (qualified_name) @import_path))

(require_once_expression
  (string) @require_path)

(require_expression
  (string) @require_path)
"""


def extract_fn_signature(source: bytes, name_node, params_node, return_node=None) -> str:
    name = source[name_node.start_byte:name_node.end_byte].decode()
    params = source[params_node.start_byte:params_node.end_byte].decode()
    ret = ""
    if return_node:
        ret = ": " + source[return_node.start_byte:return_node.end_byte].decode()
    return f"function {name}{params}{ret}"
