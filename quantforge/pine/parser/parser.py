"""Pine Script parser — converts Pine source to AST."""

from __future__ import annotations

from lark import Lark, Transformer, v_args

from quantforge.pine.parser.ast_nodes import (
    Assignment,
    BinaryOp,
    BoolLiteral,
    BreakStmt,
    ColorLiteral,
    ContinueStmt,
    FloatLiteral,
    ForLoop,
    FunctionCall,
    FunctionDef,
    Identifier,
    IfExpr,
    IntLiteral,
    MultiVarDecl,
    NaLiteral,
    NamespacedId,
    Script,
    SeriesIndex,
    StringLiteral,
    SwitchExpr,
    TernaryOp,
    UnaryOp,
    VarDecl,
    WhileLoop,
)
from quantforge.pine.parser.grammar import PINE_GRAMMAR


@v_args(inline=True)
class PineTransformer(Transformer):
    """Transform Lark parse tree into typed AST nodes."""

    def start(self, *items):
        version = 5
        body = []
        for item in items:
            if item is None:
                continue
            if isinstance(item, int):
                version = item
            elif isinstance(item, list):
                body.extend(item)
            else:
                body.append(item)
        return Script(version=version, body=body)

    def version_directive(self, v):
        return int(v)

    # --- Literals ---
    def int_lit(self, token):
        return IntLiteral(value=int(token))

    def float_lit(self, token):
        return FloatLiteral(value=float(token))

    def true_literal(self):
        return BoolLiteral(value=True)

    def false_literal(self):
        return BoolLiteral(value=False)

    def string_lit(self, token):
        s = str(token)
        if s.startswith('"') and s.endswith('"'):
            s = s[1:-1]
        return StringLiteral(value=s)

    def color_lit(self, token):
        return ColorLiteral(value=str(token))

    def na_literal(self):
        return NaLiteral()

    def identifier(self, token):
        return Identifier(name=str(token))

    # --- Expressions ---
    def member_access(self, obj, member):
        member = str(member)
        if isinstance(obj, Identifier):
            return NamespacedId(namespace=obj.name, member=member)
        if isinstance(obj, NamespacedId):
            return NamespacedId(
                namespace=f"{obj.namespace}.{obj.member}", member=member
            )
        return NamespacedId(namespace="_expr_", member=member)

    def series_index(self, series, offset):
        return SeriesIndex(series=series, offset=offset)

    def func_call(self, name, args=None):
        positional = []
        kwargs = {}
        if args is not None:
            for a in args:
                if isinstance(a, tuple) and len(a) == 2 and isinstance(a[0], str):
                    kwargs[a[0]] = a[1]
                else:
                    positional.append(a)
        return FunctionCall(name=name, args=positional, kwargs=kwargs)

    def call_args(self, *args):
        return list(args)

    def positional_arg(self, expr):
        return expr

    def kwarg(self, name, expr):
        return (str(name), expr)

    def ternary(self, cond, true_e, false_e):
        return TernaryOp(condition=cond, true_expr=true_e, false_expr=false_e)

    # --- Operator chains ---
    def or_chain(self, *args):
        return self._left_chain(args, "or")

    def and_chain(self, *args):
        return self._left_chain(args, "and")

    def not_op(self, operand):
        return UnaryOp(op="not", operand=operand)

    def comparison_chain(self, *args):
        items = list(args)
        if len(items) == 1:
            return items[0]
        result = items[0]
        i = 1
        while i < len(items):
            op = str(items[i])
            right = items[i + 1]
            result = BinaryOp(op=op, left=result, right=right)
            i += 2
        return result

    def comp_op(self, token):
        return str(token)

    def add_chain(self, *args):
        return self._interleaved_chain(args)

    def mul_chain(self, *args):
        return self._interleaved_chain(args)

    def neg(self, operand):
        return UnaryOp(op="-", operand=operand)

    def pos(self, operand):
        return operand

    def _left_chain(self, args, op):
        items = list(args)
        if len(items) == 1:
            return items[0]
        result = items[0]
        for item in items[1:]:
            result = BinaryOp(op=op, left=result, right=item)
        return result

    def _interleaved_chain(self, args):
        items = list(args)
        if len(items) == 1:
            return items[0]
        result = items[0]
        i = 1
        while i < len(items):
            op = str(items[i])
            right = items[i + 1]
            result = BinaryOp(op=op, left=result, right=right)
            i += 2
        return result

    # --- Declarations ---
    def var_decl_qual(self, qual, name, value):
        return VarDecl(qualifier=str(qual), name=str(name), value=value)

    def var_decl_typed(self, type_hint, name, value):
        return VarDecl(type_hint=str(type_hint), name=str(name), value=value)

    def var_decl_qual_typed(self, qual, type_hint, name, value):
        return VarDecl(
            qualifier=str(qual),
            type_hint=str(type_hint),
            name=str(name),
            value=value,
        )

    def qualifier(self, token):
        return str(token)

    def type_hint(self, token):
        return str(token)

    def multi_var_decl(self, *args):
        names = [str(a) for a in args[:-1]]
        return MultiVarDecl(names=names, value=args[-1])

    def assign_eq(self, name, value):
        return Assignment(target=str(name), op="=", value=value)

    def assign_reassign(self, name, value):
        return Assignment(target=str(name), op=":=", value=value)

    def assign_plus(self, name, value):
        return Assignment(target=str(name), op="+=", value=value)

    def assign_minus(self, name, value):
        return Assignment(target=str(name), op="-=", value=value)

    def assign_mul(self, name, value):
        return Assignment(target=str(name), op="*=", value=value)

    def assign_div(self, name, value):
        return Assignment(target=str(name), op="/=", value=value)

    def assign_mod(self, name, value):
        return Assignment(target=str(name), op="%=", value=value)

    # --- Control Flow ---
    def if_stmt(self, cond, then_body, *rest):
        elif_clauses = []
        else_body = None
        for item in rest:
            if isinstance(item, tuple):
                elif_clauses.append(item)
            elif isinstance(item, list):
                else_body = item
        return IfExpr(
            condition=cond,
            then_body=then_body if isinstance(then_body, list) else [then_body],
            elif_clauses=elif_clauses,
            else_body=else_body,
        )

    def elif_clause(self, cond, body):
        return (cond, body if isinstance(body, list) else [body])

    def else_clause(self, body):
        return body if isinstance(body, list) else [body]

    def for_stmt(self, var, start, stop, *rest):
        step = None
        body = []
        for item in rest:
            if isinstance(item, list):
                body = item
            elif step is None and not isinstance(item, list):
                step = item
        return ForLoop(var=str(var), start=start, stop=stop, step=step, body=body)

    def while_stmt(self, cond, body):
        return WhileLoop(
            condition=cond, body=body if isinstance(body, list) else [body]
        )

    def switch_stmt(self, *args):
        expr = None
        cases = []
        for a in args:
            if isinstance(a, list):
                cases = a
            elif not isinstance(a, list) and not isinstance(a, tuple):
                if expr is None and not isinstance(a, list):
                    expr = a
        return SwitchExpr(expr=expr, cases=cases)

    def switch_body(self, *args):
        return list(args)

    def switch_case(self, cond, body):
        return (cond, body if isinstance(body, list) else [body])

    def switch_default(self, body):
        return (None, body if isinstance(body, list) else [body])

    def break_stmt(self):
        return BreakStmt()

    def continue_stmt(self):
        return ContinueStmt()

    # --- Functions ---
    def func_def(self, name, *rest):
        params = []
        defaults = {}
        body = []
        for item in rest:
            if isinstance(item, list) and all(isinstance(x, tuple) for x in item):
                for pname, pdefault in item:
                    params.append(pname)
                    if pdefault is not None:
                        defaults[pname] = pdefault
            elif isinstance(item, list):
                body = item
            elif item is not None and not isinstance(item, list):
                body = [item]
        return FunctionDef(
            name=str(name), params=params, default_values=defaults, body=body
        )

    def func_params(self, *args):
        return list(args)

    def func_param(self, name, default=None):
        return (str(name), default)

    # --- Blocks ---
    def block(self, *stmts):
        return [s for s in stmts if s is not None]


class PineParser:
    """Parse Pine Script source code into AST."""

    def __init__(self):
        self._lark = Lark(
            PINE_GRAMMAR,
            parser="earley",
            ambiguity="resolve",
            propagate_positions=True,
        )
        self._transformer = PineTransformer()

    def parse(self, source: str) -> Script:
        """Parse Pine Script source and return AST Script node."""
        source = self._preprocess(source)
        tree = self._lark.parse(source)
        return self._transformer.transform(tree)

    def _preprocess(self, source: str) -> str:
        """Preprocess source: handle line continuations, normalize newlines."""
        source = source.replace("\r\n", "\n").replace("\r", "\n")
        # Ensure trailing newline
        if not source.endswith("\n"):
            source += "\n"
        return source
