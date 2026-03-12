"""Tests for Pine Script parser."""

from quantforge.pine.parser.ast_nodes import (
    Assignment,
    BinOp,
    BoolLiteral,
    FunctionCall,
    FunctionDef,
    Identifier,
    IfExpr,
    MemberAccess,
    NaLiteral,
    NumberLiteral,
    Script,
    StrategyDecl,
    StringLiteral,
    TernaryOp,
    UnaryOp,
    VersionDirective,
)
from quantforge.pine.parser.parser import _preprocess_indentation


# ---------------------------------------------------------------------------
# Preprocessor tests
# ---------------------------------------------------------------------------


class TestPreprocessor:
    def test_flat_code_no_indentation(self):
        src = "a = 1\nb = 2\n"
        result = _preprocess_indentation(src)
        assert "<INDENT>" not in result
        assert "<DEDENT>" not in result

    def test_if_block_produces_indent_dedent(self):
        src = "if true\n    x = 1\ny = 2\n"
        result = _preprocess_indentation(src)
        assert "<INDENT>" in result
        assert "<DEDENT>" in result

    def test_nested_indentation(self):
        src = "if true\n    if false\n        x = 1\ny = 2\n"
        result = _preprocess_indentation(src)
        assert result.count("<INDENT>") == 2
        assert result.count("<DEDENT>") >= 2


# ---------------------------------------------------------------------------
# Parser integration tests — manually constructed (no Lark parsing)
# ---------------------------------------------------------------------------


class TestASTNodes:
    def test_number_literal(self):
        n = NumberLiteral(value=42.0)
        assert n.value == 42.0

    def test_string_literal(self):
        s = StringLiteral(value="hello")
        assert s.value == "hello"

    def test_bool_literal(self):
        b = BoolLiteral(value=True)
        assert b.value is True

    def test_na_literal(self):
        na = NaLiteral()
        assert isinstance(na, NaLiteral)

    def test_identifier(self):
        ident = Identifier(name="close")
        assert ident.name == "close"

    def test_binop(self):
        b = BinOp(op="+", left=NumberLiteral(1.0), right=NumberLiteral(2.0))
        assert b.op == "+"

    def test_unaryop(self):
        u = UnaryOp(op="-", operand=NumberLiteral(5.0))
        assert u.op == "-"

    def test_ternary(self):
        t = TernaryOp(
            condition=BoolLiteral(True),
            true_expr=NumberLiteral(1.0),
            false_expr=NumberLiteral(0.0),
        )
        assert t.condition.value is True

    def test_assignment(self):
        a = Assignment(target="x", value=NumberLiteral(10.0))
        assert a.target == "x"
        assert a.op == "="

    def test_function_call(self):
        fc = FunctionCall(
            func=MemberAccess(obj=Identifier("ta"), member="sma"),
            args=[Identifier("close"), NumberLiteral(14.0)],
        )
        assert isinstance(fc.func, MemberAccess)

    def test_if_expr(self):
        ie = IfExpr(
            condition=BoolLiteral(True),
            body=[Assignment(target="x", value=NumberLiteral(1.0))],
        )
        assert len(ie.body) == 1

    def test_function_def(self):
        fd = FunctionDef(name="myFunc", body=[NumberLiteral(42.0)])
        assert fd.name == "myFunc"

    def test_script(self):
        s = Script(
            version=5,
            declarations=[StrategyDecl(kwargs={"title": StringLiteral("Test")})],
            body=[Assignment(target="x", value=NumberLiteral(1.0))],
        )
        assert s.version == 5
        assert len(s.declarations) == 1
        assert len(s.body) == 1

    def test_member_access(self):
        ma = MemberAccess(obj=Identifier("strategy"), member="long")
        assert ma.member == "long"

    def test_version_directive(self):
        vd = VersionDirective(version=6)
        assert vd.version == 6
