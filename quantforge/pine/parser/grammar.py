"""Lark grammar for Pine Script v5/v6."""

PINE_GRAMMAR = r"""
    // ====== Top Level ======
    start: (_NL)* version_directive? (_NL)* (statement (_NL)+)* statement? (_NL)*

    version_directive: "//@version=" INT

    // ====== Statements ======
    ?statement: var_decl
              | multi_var_decl
              | assignment
              | func_def
              | if_stmt
              | for_stmt
              | while_stmt
              | switch_stmt
              | break_stmt
              | continue_stmt
              | expr

    var_decl: qualifier NAME "=" expr           -> var_decl_qual
            | type_hint NAME "=" expr           -> var_decl_typed
            | qualifier type_hint NAME "=" expr -> var_decl_qual_typed

    qualifier: QUALIFIER
    type_hint: TYPE_HINT
    QUALIFIER: "var" | "varip"
    TYPE_HINT: "int" | "float" | "bool" | "string" | "color" | "series"

    multi_var_decl: "[" NAME ("," NAME)+ "]" "=" expr

    assignment: NAME "=" expr             -> assign_eq
             | NAME ":=" expr             -> assign_reassign
             | NAME "+=" expr             -> assign_plus
             | NAME "-=" expr             -> assign_minus
             | NAME "*=" expr             -> assign_mul
             | NAME "/=" expr             -> assign_div
             | NAME "%=" expr             -> assign_mod

    // ====== Function Definition ======
    func_def: NAME "(" func_params? ")" "=>" (_NL)? (indented_block | expr)
    func_params: func_param ("," func_param)*
    func_param: NAME ("=" expr)?

    // ====== Control Flow ======
    if_stmt: "if" expr (_NL)+ indented_block elif_clause* else_clause?
    elif_clause: "else" "if" expr (_NL)+ indented_block
    else_clause: "else" (_NL)+ indented_block

    for_stmt: "for" NAME "=" expr "to" expr ("by" expr)? (_NL)+ indented_block
    while_stmt: "while" expr (_NL)+ indented_block
    switch_stmt: "switch" expr? (_NL)+ switch_body
    switch_body: (switch_case (_NL)+)* switch_default?
    switch_case: INDENT expr "=>" (_NL)? (indented_block2 | expr)
    switch_default: INDENT "=>" (_NL)? (indented_block2 | expr)

    break_stmt: "break"
    continue_stmt: "continue"

    // ====== Indented Blocks ======
    indented_block: INDENT statement (_NL+ INDENT statement)* _NL? -> block
    indented_block2: INDENT INDENT statement (_NL+ INDENT INDENT statement)* _NL? -> block

    // ====== Expressions (precedence climbing) ======
    ?expr: ternary_expr

    ?ternary_expr: or_expr "?" ternary_expr ":" ternary_expr -> ternary
                 | or_expr

    ?or_expr: and_expr ("or" and_expr)*            -> or_chain
    ?and_expr: not_expr ("and" not_expr)*           -> and_chain
    ?not_expr: "not" not_expr                       -> not_op
             | comparison

    ?comparison: add_expr (comp_op add_expr)*       -> comparison_chain
    comp_op: COMP_OP
    COMP_OP: "==" | "!=" | "<=" | ">=" | "<" | ">"

    ?add_expr: mul_expr (ADD_OP mul_expr)*         -> add_chain
    ?mul_expr: unary_expr (MUL_OP unary_expr)*     -> mul_chain

    ADD_OP: "+" | "-"
    MUL_OP: "*" | "/" | "%"

    ?unary_expr: "-" unary_expr                     -> neg
              | "+" unary_expr                      -> pos
              | postfix_expr

    ?postfix_expr: postfix_expr "[" expr "]"        -> series_index
                 | postfix_expr "(" call_args? ")"  -> func_call
                 | postfix_expr "." NAME            -> member_access
                 | atom

    // ====== Call Arguments ======
    call_args: call_arg ("," call_arg)*
    call_arg: NAME "=" expr -> kwarg
            | expr          -> positional_arg

    // ====== Atoms ======
    ?atom: "(" expr ")"
         | "na"                          -> na_literal
         | "true"                        -> true_literal
         | "false"                       -> false_literal
         | FLOAT_NUMBER                  -> float_lit
         | INT                           -> int_lit
         | ESCAPED_STRING                -> string_lit
         | COLOR_LITERAL                 -> color_lit
         | NAME                          -> identifier

    // ====== Terminals ======
    INDENT: /    |\t/
    COLOR_LITERAL: /#[0-9a-fA-F]{6,8}/

    // Line continuation: backslash + newline is ignored
    _NL: /[ \t]*\n/
    LINE_CONTINUATION: /\\\n/

    // Comments (exclude version directives //@version=N)
    COMMENT: /\/\/(?!@version=).*/
    BLOCK_COMMENT: /\/\*[\s\S]*?\*\//

    %import common.CNAME -> NAME
    %import common.INT
    %import common.FLOAT -> FLOAT_NUMBER
    %import common.ESCAPED_STRING
    %import common.WS_INLINE

    %ignore WS_INLINE
    %ignore LINE_CONTINUATION
    %ignore COMMENT
    %ignore BLOCK_COMMENT
"""
