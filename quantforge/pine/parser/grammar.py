"""Lark PEG grammar for Pine Script v5/v6."""

PINE_GRAMMAR = r"""
    start: (_NL* version_directive)? (_NL* top_decl)? (_NL* statement)*

    version_directive: "//@version=" INT

    top_decl: strategy_decl | indicator_decl

    indicator_decl: "indicator" "(" func_call_args ")"
    strategy_decl: "strategy" "(" func_call_args ")"

    ?statement: assignment_stmt
              | reassignment_stmt
              | func_def
              | if_stmt
              | for_stmt
              | for_in_stmt
              | while_stmt
              | break_stmt
              | continue_stmt
              | tuple_assign_stmt
              | expr_stmt

    break_stmt: "break"
    continue_stmt: "continue"

    assignment_stmt: (DECLARATION)? (TYPE_HINT)? NAME ASSIGN_OP expr
    reassignment_stmt: NAME REASSIGN_OP expr

    DECLARATION: "var" | "varip"
    TYPE_HINT: "int" | "float" | "bool" | "string" | "color" | "series float" | "series int" | "series bool"
    ASSIGN_OP: "=" | "+=" | "-=" | "*=" | "/="
    REASSIGN_OP: ":="

    tuple_assign_stmt: "[" NAME ("," NAME)+ "]" "=" expr

    func_def: NAME "(" func_def_params? ")" "=>" (expr | _NL _INDENT (_NL* statement)+ _NL* _DEDENT)

    func_def_params: func_def_param ("," func_def_param)*
    func_def_param: NAME ("=" expr)?

    if_stmt: "if" expr _NL _INDENT (_NL* statement)+ _NL* _DEDENT (else_if_clause)* (else_clause)?
    else_if_clause: "else" "if" expr _NL _INDENT (_NL* statement)+ _NL* _DEDENT
    else_clause: "else" _NL _INDENT (_NL* statement)+ _NL* _DEDENT

    for_stmt: "for" NAME "=" expr "to" expr ("by" expr)? _NL _INDENT (_NL* statement)+ _NL* _DEDENT
    for_in_stmt: "for" NAME "in" expr _NL _INDENT (_NL* statement)+ _NL* _DEDENT
    while_stmt: "while" expr _NL _INDENT (_NL* statement)+ _NL* _DEDENT

    expr_stmt: expr

    // --- Expressions (precedence climbing) ---
    ?expr: ternary_expr

    ?ternary_expr: or_expr ("?" ternary_expr ":" ternary_expr)?

    ?or_expr: and_expr ("or" and_expr)*
    ?and_expr: not_expr ("and" not_expr)*
    ?not_expr: "not" not_expr -> unary_not
             | comparison

    ?comparison: add_expr (COMP_OP add_expr)*
    COMP_OP: "==" | "!=" | ">=" | "<=" | ">" | "<"

    ?add_expr: mul_expr (ADD_OP mul_expr)*
    ADD_OP: "+" | "-"
    ?mul_expr: unary_expr (MUL_OP unary_expr)*
    MUL_OP: "*" | "/" | "%"

    ?unary_expr: "-" unary_expr -> unary_neg
               | postfix_expr

    ?postfix_expr: postfix_expr "[" expr "]" -> index_access
                 | postfix_expr "." NAME -> member_access
                 | postfix_expr "(" func_call_args ")" -> func_call
                 | atom

    func_call_args: (func_call_arg ("," func_call_arg)*)?
    func_call_args_ne: func_call_arg ("," func_call_arg)*
    func_call_arg: (NAME "=")? expr

    ?atom: "(" expr ")"
         | "na" -> na_literal
         | "true" -> true_literal
         | "false" -> false_literal
         | ESCAPED_STRING -> string_literal
         | COLOR_LITERAL -> color_literal
         | NUMBER -> number_literal
         | NAME -> identifier

    COLOR_LITERAL: "#" /[0-9a-fA-F]{6,8}/

    // --- Terminals ---
    %import common.CNAME -> NAME
    %import common.NUMBER
    %import common.INT
    %import common.ESCAPED_STRING
    %import common.WS_INLINE

    _NL: /(\s*\n)+/

    COMMENT: "//" /[^\n]/*
    %ignore COMMENT
    %ignore WS_INLINE

    _INDENT: "<INDENT>"
    _DEDENT: "<DEDENT>"
"""
