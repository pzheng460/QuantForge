"""Tests for the Pine Script parser."""

from __future__ import annotations

import pytest

from quantforge.pine.parser.ast_nodes import (
    Assignment,
    FunctionDef,
    Script,
    VarDecl,
)
from quantforge.pine.parser.parser import PineParser


@pytest.fixture
def parser():
    return PineParser()


class TestLiterals:
    def test_int_literal(self, parser):
        ast = parser.parse("x = 42\n")
        assert isinstance(ast, Script)
        # Find the assignment/decl
        assert len(ast.body) >= 1

    def test_float_literal(self, parser):
        ast = parser.parse("x = 3.14\n")
        assert isinstance(ast, Script)

    def test_bool_literal(self, parser):
        ast = parser.parse("x = true\n")
        assert isinstance(ast, Script)

    def test_string_literal(self, parser):
        ast = parser.parse('x = "hello"\n')
        assert isinstance(ast, Script)

    def test_na_literal(self, parser):
        ast = parser.parse("x = na\n")
        assert isinstance(ast, Script)


class TestVersionDirective:
    def test_v5(self, parser):
        ast = parser.parse("//@version=5\nx = 1\n")
        assert ast.version == 5

    def test_v6(self, parser):
        ast = parser.parse("//@version=6\nx = 1\n")
        assert ast.version == 6


class TestExpressions:
    def test_binary_arithmetic(self, parser):
        ast = parser.parse("x = 1 + 2\n")
        assert isinstance(ast, Script)
        assert len(ast.body) >= 1

    def test_comparison(self, parser):
        ast = parser.parse("x = 1 > 2\n")
        assert isinstance(ast, Script)

    def test_logical(self, parser):
        ast = parser.parse("x = true and false\n")
        assert isinstance(ast, Script)

    def test_ternary(self, parser):
        ast = parser.parse("x = true ? 1 : 2\n")
        assert isinstance(ast, Script)

    def test_negation(self, parser):
        ast = parser.parse("x = -5\n")
        assert isinstance(ast, Script)

    def test_not(self, parser):
        ast = parser.parse("x = not true\n")
        assert isinstance(ast, Script)


class TestSeriesIndex:
    def test_close_offset(self, parser):
        ast = parser.parse("x = close[1]\n")
        assert isinstance(ast, Script)

    def test_nested_index(self, parser):
        ast = parser.parse("x = high[2]\n")
        assert isinstance(ast, Script)


class TestFunctionCalls:
    def test_namespaced_call(self, parser):
        ast = parser.parse("x = ta.sma(close, 14)\n")
        assert isinstance(ast, Script)

    def test_kwargs(self, parser):
        ast = parser.parse('x = input.int(defval=14, title="Length")\n')
        assert isinstance(ast, Script)

    def test_strategy_call(self, parser):
        ast = parser.parse('strategy.entry("Long", strategy.long)\n')
        assert isinstance(ast, Script)

    def test_member_access(self, parser):
        ast = parser.parse("x = strategy.position_size\n")
        assert isinstance(ast, Script)


class TestDeclarations:
    def test_var_decl(self, parser):
        ast = parser.parse("var x = 0\n")
        assert isinstance(ast, Script)
        decl = ast.body[0]
        assert isinstance(decl, VarDecl)
        assert decl.qualifier == "var"
        assert decl.name == "x"

    def test_typed_decl(self, parser):
        ast = parser.parse("float x = 0.0\n")
        assert isinstance(ast, Script)
        decl = ast.body[0]
        assert isinstance(decl, VarDecl)
        assert decl.type_hint == "float"

    def test_reassignment(self, parser):
        ast = parser.parse("x := 5\n")
        assert isinstance(ast, Script)
        stmt = ast.body[0]
        assert isinstance(stmt, Assignment)
        assert stmt.op == ":="


class TestControlFlow:
    def test_if_simple(self, parser):
        src = "if true\n    x = 1\n"
        ast = parser.parse(src)
        assert isinstance(ast, Script)

    def test_if_else(self, parser):
        src = "if true\n    x = 1\nelse\n    x = 2\n"
        ast = parser.parse(src)
        assert isinstance(ast, Script)

    def test_for_loop(self, parser):
        src = "for i = 0 to 10\n    x = i\n"
        ast = parser.parse(src)
        assert isinstance(ast, Script)

    def test_while_loop(self, parser):
        src = "while true\n    break\n"
        ast = parser.parse(src)
        assert isinstance(ast, Script)


class TestFunctionDef:
    def test_simple_function(self, parser):
        src = "my_func(x, y) =>\n    x + y\n"
        ast = parser.parse(src)
        assert isinstance(ast, Script)
        func = ast.body[0]
        assert isinstance(func, FunctionDef)
        assert func.name == "my_func"
        assert func.params == ["x", "y"]


class TestFullScript:
    def test_ema_cross_structure(self, parser):
        src = """//@version=5
strategy("EMA Cross", overlay=true)
fast = ta.ema(close, 9)
slow = ta.ema(close, 21)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)
"""
        ast = parser.parse(src)
        assert isinstance(ast, Script)
        assert ast.version == 5
        # strategy() call + 2 var assignments + if statement = 4
        assert len(ast.body) >= 4
