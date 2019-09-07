from typing import Any, Dict, List, Optional, Union, cast

from ..error import GraphQLError, INVALID
from ..language import (
    DirectiveNode,
    ExecutableDefinitionNode,
    FieldNode,
    NullValueNode,
    SchemaDefinitionNode,
    SelectionNode,
    TypeDefinitionNode,
    TypeExtensionNode,
    VariableDefinitionNode,
    VariableNode,
    print_ast,
)
from ..pyutils import inspect, FrozenList
from ..type import (
    GraphQLDirective,
    GraphQLField,
    GraphQLInputType,
    GraphQLSchema,
    is_input_type,
    is_non_null_type,
)
from ..utilities import coerce_value, type_from_ast, value_from_ast

__all__ = ["get_variable_values", "get_argument_values", "get_directive_values"]


CoercedVariableValues = Union[List[GraphQLError], Dict[str, Any]]


def get_variable_values(
    schema: GraphQLSchema,
    var_def_nodes: FrozenList[VariableDefinitionNode],
    inputs: Dict[str, Any],
) -> CoercedVariableValues:
    """Get coerced variable values based on provided definitions.

    Prepares a dict of variable values of the correct type based on the provided
    variable definitions and arbitrary input. If the input cannot be parsed to match
    the variable definitions, a GraphQLError will be thrown.
    """
    errors: List[GraphQLError] = []
    coerced_values: Dict[str, Any] = {}
    for var_def_node in var_def_nodes:
        var_name = var_def_node.variable.name.value
        var_type = type_from_ast(schema, var_def_node.type)
        if not is_input_type(var_type):
            # Must use input types for variables. This should be caught during
            # validation, however is checked again here for safety.
            var_type_str = print_ast(var_def_node.type)
            errors.append(
                GraphQLError(
                    f"Variable '${var_name}' expected value of type '{var_type_str}'"
                    " which cannot be used as an input type.",
                    var_def_node.type,
                )
            )
            continue

        var_type = cast(GraphQLInputType, var_type)
        if var_name not in inputs:
            if var_def_node.default_value:
                coerced_values[var_name] = value_from_ast(
                    var_def_node.default_value, var_type
                )

            if is_non_null_type(var_type):
                var_type_str = inspect(var_type)
                errors.append(
                    GraphQLError(
                        f"Variable '${var_name}' of required type '{var_type_str}'"
                        " was not provided.",
                        var_def_node,
                    )
                )
            continue

        value = inputs[var_name]
        if value is None and is_non_null_type(var_type):
            var_type_str = inspect(var_type)
            errors.append(
                GraphQLError(
                    f"Variable '${var_name}' of non-null type '{var_type_str}'"
                    " must not be null.",
                    var_def_node,
                )
            )
            continue

        coerced = coerce_value(value, var_type, var_def_node)
        coercion_errors = coerced.errors
        if coercion_errors:
            for error in coercion_errors:
                error.message = (
                    f"Variable '${var_name}' got invalid"
                    f" value {inspect(value)}; {error.message}"
                )
            errors.extend(coercion_errors)
            continue

        coerced_values[var_name] = coerced.value

    return errors or coerced_values


def get_argument_values(
    type_def: Union[GraphQLField, GraphQLDirective],
    node: Union[FieldNode, DirectiveNode],
    variable_values: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """Get coerced argument values based on provided definitions and nodes.

    Prepares an dict of argument values given a list of argument definitions and list
    of argument AST nodes.
    """
    coerced_values: Dict[str, Any] = {}
    arg_node_map = {arg.name.value: arg for arg in node.arguments or []}

    for name, arg_def in type_def.args.items():
        arg_type = arg_def.type
        argument_node = arg_node_map.get(name)

        if argument_node is None:
            if arg_def.default_value is not INVALID:
                coerced_values[arg_def.out_name or name] = arg_def.default_value
            elif is_non_null_type(arg_type):
                raise GraphQLError(
                    f"Argument '{name}' of required type '{arg_type}'"
                    " was not provided.",
                    node,
                )
            continue

        value_node = argument_node.value
        is_null = isinstance(argument_node.value, NullValueNode)

        if isinstance(value_node, VariableNode):
            variable_name = value_node.name.value
            if variable_values is None or variable_name not in variable_values:
                if arg_def.default_value is not INVALID:
                    coerced_values[arg_def.out_name or name] = arg_def.default_value
                elif is_non_null_type(arg_type):
                    raise GraphQLError(
                        f"Argument '{name}' of required type '{arg_type}'"
                        f" was provided the variable '${variable_name}'"
                        " which was not provided a runtime value.",
                        value_node,
                    )
                continue
            is_null = variable_values[variable_name] is None

        if is_null and is_non_null_type(arg_type):
            raise GraphQLError(
                f"Argument '{name}' of non-null type '{arg_type}' must not be null.",
                value_node,
            )

        coerced_value = value_from_ast(value_node, arg_type, variable_values)
        if coerced_value is INVALID:
            # Note: `values_of_correct_type` validation should catch this before
            # execution. This is a runtime check to ensure execution does not
            # continue with an invalid argument value.
            raise GraphQLError(
                f"Argument '{name}' has invalid value {print_ast(value_node)}.",
                value_node,
            )
        coerced_values[arg_def.out_name or name] = coerced_value

    return coerced_values


NodeWithDirective = Union[
    ExecutableDefinitionNode,
    SelectionNode,
    SchemaDefinitionNode,
    TypeDefinitionNode,
    TypeExtensionNode,
]


def get_directive_values(
    directive_def: GraphQLDirective,
    node: NodeWithDirective,
    variable_values: Dict[str, Any] = None,
) -> Optional[Dict[str, Any]]:
    """Get coerced argument values based on provided nodes.

    Prepares a dict of argument values given a directive definition and an AST node
    which may contain directives. Optionally also accepts a dict of variable values.

    If the directive does not exist on the node, returns None.
    """
    directives = node.directives
    if directives:
        directive_name = directive_def.name
        for directive in directives:
            if directive.name.value == directive_name:
                return get_argument_values(directive_def, directive, variable_values)
    return None