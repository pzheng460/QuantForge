"""AST node dataclasses for Pine Script v5/v6."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ASTNode:
    """Base class for all AST nodes."""


# --- Literals ---


@dataclass
class NumberLiteral(ASTNode):
    value: float


@dataclass
class StringLiteral(ASTNode):
    value: str


@dataclass
class BoolLiteral(ASTNode):
    value: bool


@dataclass
class ColorLiteral(ASTNode):
    value: str


@dataclass
class NaLiteral(ASTNode):
    pass


# --- Identifiers / access ---


@dataclass
class Identifier(ASTNode):
    name: str


@dataclass
class MemberAccess(ASTNode):
    obj: ASTNode
    member: str


@dataclass
class IndexAccess(ASTNode):
    obj: ASTNode
    index: ASTNode


# --- Expressions ---


@dataclass
class BinOp(ASTNode):
    op: str
    left: ASTNode
    right: ASTNode


@dataclass
class UnaryOp(ASTNode):
    op: str
    operand: ASTNode


@dataclass
class TernaryOp(ASTNode):
    condition: ASTNode
    true_expr: ASTNode
    false_expr: ASTNode


@dataclass
class FunctionCall(ASTNode):
    func: ASTNode
    args: list[ASTNode] = field(default_factory=list)
    kwargs: dict[str, ASTNode] = field(default_factory=dict)


@dataclass
class Assignment(ASTNode):
    target: str
    value: ASTNode
    declaration: str | None = None
    type_hint: str | None = None
    op: str = "="


@dataclass
class TupleAssignment(ASTNode):
    targets: list[str]
    value: ASTNode


# --- Control flow ---


@dataclass
class IfExpr(ASTNode):
    condition: ASTNode
    body: list[ASTNode]
    elseif_clauses: list[tuple[ASTNode, list[ASTNode]]] = field(default_factory=list)
    else_body: list[ASTNode] | None = None


@dataclass
class ForLoop(ASTNode):
    var: str
    start: ASTNode
    stop: ASTNode
    step: ASTNode | None = None
    body: list[ASTNode] = field(default_factory=list)


@dataclass
class ForInLoop(ASTNode):
    var: str
    iterable: ASTNode
    body: list[ASTNode] = field(default_factory=list)


@dataclass
class WhileLoop(ASTNode):
    condition: ASTNode
    body: list[ASTNode] = field(default_factory=list)


@dataclass
class BreakStmt(ASTNode):
    pass


@dataclass
class ContinueStmt(ASTNode):
    pass


# --- Functions ---


@dataclass
class FunctionDef(ASTNode):
    name: str
    params: list[FunctionParam] = field(default_factory=list)
    body: list[ASTNode] = field(default_factory=list)
    return_type: str | None = None


@dataclass
class FunctionParam(ASTNode):
    name: str
    default: ASTNode | None = None
    type_hint: str | None = None


# --- Top-level ---


@dataclass
class VersionDirective(ASTNode):
    version: int


@dataclass
class IndicatorDecl(ASTNode):
    kwargs: dict[str, ASTNode] = field(default_factory=dict)


@dataclass
class StrategyDecl(ASTNode):
    kwargs: dict[str, ASTNode] = field(default_factory=dict)


@dataclass
class Script(ASTNode):
    version: int = 5
    declarations: list[ASTNode] = field(default_factory=list)
    body: list[ASTNode] = field(default_factory=list)
