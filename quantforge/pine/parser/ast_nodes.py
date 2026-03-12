"""Typed AST nodes for Pine Script."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# --- Base ---


@dataclass
class ASTNode:
    """Base AST node."""

    line: int = 0
    col: int = 0


# --- Literals ---


@dataclass
class IntLiteral(ASTNode):
    value: int = 0


@dataclass
class FloatLiteral(ASTNode):
    value: float = 0.0


@dataclass
class BoolLiteral(ASTNode):
    value: bool = False


@dataclass
class StringLiteral(ASTNode):
    value: str = ""


@dataclass
class ColorLiteral(ASTNode):
    value: str = ""  # e.g. "#FF0000" or color.red


@dataclass
class NaLiteral(ASTNode):
    """Pine Script `na` value."""


# --- Identifiers & Access ---


@dataclass
class Identifier(ASTNode):
    name: str = ""


@dataclass
class NamespacedId(ASTNode):
    """Dotted identifier: ta.sma, strategy.long, math.abs, etc."""

    namespace: str = ""
    member: str = ""


@dataclass
class SeriesIndex(ASTNode):
    """series[offset] — historical bar access."""

    series: ASTNode = field(default_factory=ASTNode)
    offset: ASTNode = field(default_factory=ASTNode)


# --- Expressions ---


@dataclass
class BinaryOp(ASTNode):
    op: str = ""
    left: ASTNode = field(default_factory=ASTNode)
    right: ASTNode = field(default_factory=ASTNode)


@dataclass
class UnaryOp(ASTNode):
    op: str = ""  # "-", "not"
    operand: ASTNode = field(default_factory=ASTNode)


@dataclass
class TernaryOp(ASTNode):
    condition: ASTNode = field(default_factory=ASTNode)
    true_expr: ASTNode = field(default_factory=ASTNode)
    false_expr: ASTNode = field(default_factory=ASTNode)


@dataclass
class FunctionCall(ASTNode):
    name: ASTNode = field(default_factory=ASTNode)  # Identifier or NamespacedId
    args: list[ASTNode] = field(default_factory=list)
    kwargs: dict[str, ASTNode] = field(default_factory=dict)


@dataclass
class Assignment(ASTNode):
    target: str = ""
    value: ASTNode = field(default_factory=ASTNode)
    op: str = "="  # =, :=, +=, -=, *=, /=


@dataclass
class VarDecl(ASTNode):
    """var x = expr  or  varip x = expr  or  type x = expr."""

    qualifier: str = ""  # "var", "varip", or "" for regular
    type_hint: Optional[str] = None  # int, float, bool, string, etc.
    name: str = ""
    value: ASTNode = field(default_factory=ASTNode)


@dataclass
class MultiVarDecl(ASTNode):
    """[a, b, c] = func() — tuple unpacking."""

    names: list[str] = field(default_factory=list)
    value: ASTNode = field(default_factory=ASTNode)


# --- Control Flow ---


@dataclass
class IfExpr(ASTNode):
    condition: ASTNode = field(default_factory=ASTNode)
    then_body: list[ASTNode] = field(default_factory=list)
    elif_clauses: list[tuple[ASTNode, list[ASTNode]]] = field(default_factory=list)
    else_body: Optional[list[ASTNode]] = None


@dataclass
class ForLoop(ASTNode):
    var: str = ""
    start: ASTNode = field(default_factory=ASTNode)
    stop: ASTNode = field(default_factory=ASTNode)
    step: Optional[ASTNode] = None
    body: list[ASTNode] = field(default_factory=list)


@dataclass
class WhileLoop(ASTNode):
    condition: ASTNode = field(default_factory=ASTNode)
    body: list[ASTNode] = field(default_factory=list)


@dataclass
class SwitchExpr(ASTNode):
    expr: Optional[ASTNode] = None  # None for switch without expression
    cases: list[tuple[Optional[ASTNode], list[ASTNode]]] = field(
        default_factory=list
    )  # (condition, body); None condition = default


@dataclass
class BreakStmt(ASTNode):
    pass


@dataclass
class ContinueStmt(ASTNode):
    pass


# --- Functions ---


@dataclass
class FunctionDef(ASTNode):
    name: str = ""
    params: list[str] = field(default_factory=list)
    default_values: dict[str, ASTNode] = field(default_factory=dict)
    body: list[ASTNode] = field(default_factory=list)


# --- Top Level ---


@dataclass
class VersionDirective(ASTNode):
    version: int = 5


@dataclass
class Script(ASTNode):
    """Root node for a complete Pine Script."""

    version: int = 5
    body: list[ASTNode] = field(default_factory=list)
