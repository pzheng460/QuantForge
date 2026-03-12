"""Pine Script source → AST parser.

Uses a two-pass approach:
1. Preprocessor: convert indentation to <INDENT>/<DEDENT> tokens
2. Lark parser: parse the tokenised source into a CST
3. Transformer: convert CST → typed AST nodes
"""

from __future__ import annotations


from lark import Lark, Token, Transformer, v_args

from quantforge.pine.parser.ast_nodes import (
    Assignment,
    ASTNode,
    BinOp,
    BoolLiteral,
    BreakStmt,
    ColorLiteral,
    ContinueStmt,
    ForLoop,
    ForInLoop,
    FunctionCall,
    FunctionDef,
    FunctionParam,
    Identifier,
    IfExpr,
    IndexAccess,
    IndicatorDecl,
    MemberAccess,
    NaLiteral,
    NumberLiteral,
    Script,
    StrategyDecl,
    StringLiteral,
    TernaryOp,
    TupleAssignment,
    UnaryOp,
    VersionDirective,
    WhileLoop,
)
from quantforge.pine.parser.grammar import PINE_GRAMMAR


# ---------------------------------------------------------------------------
# Indentation preprocessor
# ---------------------------------------------------------------------------


def _preprocess_indentation(source: str) -> str:
    """Convert Python-style indentation to explicit <INDENT>/<DEDENT> tokens."""
    lines = source.split("\n")
    result: list[str] = []
    indent_stack: list[int] = [0]

    for raw_line in lines:
        # Skip blank / comment-only lines
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("//"):
            result.append(raw_line)
            continue

        # Measure indentation (spaces only – Pine uses 4 spaces)
        indent = len(raw_line) - len(raw_line.lstrip())

        if indent > indent_stack[-1]:
            indent_stack.append(indent)
            result.append("<INDENT>" + raw_line.lstrip())
        elif indent < indent_stack[-1]:
            while indent_stack and indent_stack[-1] > indent:
                indent_stack.pop()
                result.append("<DEDENT>")
            result.append(raw_line.lstrip())
        else:
            result.append(raw_line.lstrip())

    # Close remaining indents
    while len(indent_stack) > 1:
        indent_stack.pop()
        result.append("<DEDENT>")

    return "\n".join(result)


# ---------------------------------------------------------------------------
# CST → AST transformer
# ---------------------------------------------------------------------------


@v_args(inline=True)
class PineTransformer(Transformer):
    """Transform Lark parse tree into AST nodes."""

    # --- literals ---
    def number_literal(self, token):
        return NumberLiteral(value=float(token))

    def string_literal(self, token):
        s = str(token)
        if s.startswith('"') and s.endswith('"'):
            s = s[1:-1]
        return StringLiteral(value=s)

    def true_literal(self):
        return BoolLiteral(value=True)

    def false_literal(self):
        return BoolLiteral(value=False)

    def na_literal(self):
        return NaLiteral()

    def color_literal(self, token):
        return ColorLiteral(value=str(token))

    def identifier(self, name):
        return Identifier(name=str(name))

    # --- access ---
    def member_access(self, obj, member):
        return MemberAccess(obj=obj, member=str(member))

    def index_access(self, obj, index):
        return IndexAccess(obj=obj, index=index)

    # --- expressions ---
    def add_expr(self, *args):
        return self._left_binop(args)

    def mul_expr(self, *args):
        return self._left_binop(args)

    def comparison(self, *args):
        return self._left_binop(args)

    def or_expr(self, *args):
        return self._chain_binop(args, "or")

    def and_expr(self, *args):
        return self._chain_binop(args, "and")

    def unary_neg(self, operand):
        return UnaryOp(op="-", operand=operand)

    def unary_not(self, operand):
        return UnaryOp(op="not", operand=operand)

    def ternary_expr(self, cond, true_e, false_e):
        return TernaryOp(condition=cond, true_expr=true_e, false_expr=false_e)

    # --- function calls ---
    def func_call(self, func_expr, args_node):
        args, kwargs = self._extract_call_args(args_node)
        return FunctionCall(func=func_expr, args=args, kwargs=kwargs)

    def func_call_args(self, *items):
        return list(items)

    def func_call_args_ne(self, *items):
        return list(items)

    def func_call_arg(self, *parts):
        if len(parts) == 2:
            return (str(parts[0]), parts[1])  # keyword arg
        return parts[0]  # positional arg

    # --- assignments ---
    def assignment_stmt(self, *parts):
        decl = None
        type_hint = None
        idx = 0
        for p in parts:
            if isinstance(p, Token) and p.type == "DECLARATION":
                decl = str(p).strip()
                idx += 1
            elif isinstance(p, Token) and p.type == "TYPE_HINT":
                type_hint = str(p).strip()
                idx += 1
            else:
                break
        name = str(parts[idx])
        op = str(parts[idx + 1]).strip()
        value = parts[idx + 2]
        return Assignment(
            target=name, value=value, declaration=decl, type_hint=type_hint, op=op
        )

    def reassignment_stmt(self, name, _op, value):
        return Assignment(target=str(name), value=value, op=":=")

    def tuple_assign_stmt(self, *parts):
        targets = [str(p) for p in parts[:-1]]
        value = parts[-1]
        return TupleAssignment(targets=targets, value=value)

    # --- control flow ---
    def if_stmt(self, *parts):
        condition = parts[0]
        body: list[ASTNode] = []
        elseif_clauses: list[tuple[ASTNode, list[ASTNode]]] = []
        else_body: list[ASTNode] | None = None

        i = 1
        # Collect body statements
        while i < len(parts) and not isinstance(parts[i], (list, tuple)):
            if isinstance(parts[i], ASTNode):
                body.append(parts[i])
            i += 1

        # elseif / else
        while i < len(parts):
            item = parts[i]
            if isinstance(item, tuple) and len(item) == 2:
                elseif_clauses.append(item)
            elif isinstance(item, list):
                else_body = item
            i += 1

        return IfExpr(
            condition=condition,
            body=body,
            elseif_clauses=elseif_clauses,
            else_body=else_body,
        )

    def else_if_clause(self, cond, *stmts):
        return (cond, list(stmts))

    def else_clause(self, *stmts):
        return list(stmts)

    def for_stmt(self, var, start, stop, *rest):
        step = None
        body = []
        for item in rest:
            if isinstance(item, ASTNode) and not isinstance(item, (ForLoop,)):
                if step is None and not body:
                    # Could be step or body element
                    # If we already have a step, it's body
                    if isinstance(item, NumberLiteral):
                        step = item
                    else:
                        body.append(item)
                else:
                    body.append(item)
            elif isinstance(item, ASTNode):
                body.append(item)
        return ForLoop(var=str(var), start=start, stop=stop, step=step, body=body)

    def for_in_stmt(self, var, iterable, *body):
        return ForInLoop(var=str(var), iterable=iterable, body=list(body))

    def while_stmt(self, cond, *body):
        return WhileLoop(condition=cond, body=list(body))

    def break_stmt(self):
        return BreakStmt()

    def continue_stmt(self):
        return ContinueStmt()

    # --- functions ---
    def func_def(self, name, *rest):
        params = []
        body = []
        for item in rest:
            if isinstance(item, list) and all(
                isinstance(p, FunctionParam) for p in item
            ):
                params = item
            elif isinstance(item, FunctionParam):
                params.append(item)
            elif isinstance(item, ASTNode):
                body.append(item)
        return FunctionDef(name=str(name), params=params, body=body)

    def func_def_params(self, *params):
        return list(params)

    def func_def_param(self, *parts):
        name = str(parts[0])
        default = parts[1] if len(parts) > 1 else None
        return FunctionParam(name=name, default=default)

    # --- top-level ---
    def version_directive(self, version):
        return VersionDirective(version=int(version))

    def indicator_decl(self, args_node):
        _, kwargs = self._extract_call_args(args_node)
        return IndicatorDecl(kwargs=kwargs)

    def strategy_decl(self, args_node):
        _, kwargs = self._extract_call_args(args_node)
        return StrategyDecl(kwargs=kwargs)

    def top_decl(self, decl):
        return decl

    def expr_stmt(self, expr):
        return expr

    def start(self, *items):
        version = 5
        declarations = []
        body = []
        for item in items:
            if isinstance(item, VersionDirective):
                version = item.version
            elif isinstance(item, (IndicatorDecl, StrategyDecl)):
                declarations.append(item)
            elif isinstance(item, ASTNode):
                body.append(item)
        return Script(version=version, declarations=declarations, body=body)

    # --- helpers ---

    @staticmethod
    def _left_binop(args):
        """Build left-associative binary operations from interleaved (expr op expr op ...) list."""
        result = args[0]
        i = 1
        while i < len(args):
            op = str(args[i])
            right = args[i + 1]
            result = BinOp(op=op, left=result, right=right)
            i += 2
        return result

    @staticmethod
    def _chain_binop(args, op: str):
        """Chain binary operations for and/or which don't interleave operator tokens."""
        result = args[0]
        for i in range(1, len(args)):
            result = BinOp(op=op, left=result, right=args[i])
        return result

    @staticmethod
    def _extract_call_args(args_node) -> tuple[list, dict]:
        """Extract positional and keyword arguments from func_call_args."""
        if args_node is None:
            return [], {}
        items = args_node if isinstance(args_node, list) else [args_node]
        args = []
        kwargs = {}
        for item in items:
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str):
                kwargs[item[0]] = item[1]
            else:
                args.append(item)
        return args, kwargs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_parser: Lark | None = None


def _get_parser() -> Lark:
    global _parser
    if _parser is None:
        _parser = Lark(
            PINE_GRAMMAR,
            parser="earley",
            ambiguity="resolve",
            propagate_positions=True,
        )
    return _parser


def parse(source: str) -> Script:
    """Parse Pine Script source code and return an AST Script node."""
    preprocessed = _preprocess_indentation(source)
    tree = _get_parser().parse(preprocessed)
    return PineTransformer().transform(tree)
