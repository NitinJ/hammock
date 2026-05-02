"""Predicate grammar evaluator.

Per design doc § Plan Compiler § Predicate grammar — deliberately minimal:

| Construct                    | Example                             |
|------------------------------|-------------------------------------|
| Dotted-path access into JSON | ``design-review.json.verdict``      |
| Equality / inequality        | ``==``, ``!=``                      |
| String literals              | ``'approved'``, ``"rejected"``      |
| Boolean literals             | ``true``, ``false``                 |
| Logical combinators          | ``and``, ``or``, ``not``            |

No arithmetic, no function calls, no list comprehensions. Used by the Plan
Compiler at evaluation time; ``runs_if`` and ``loop_back.condition``
predicates are parsed at compile time and evaluated at stage-completion time
against an artifact dict.

Stage 0 ships the parser + evaluator; Stage 3 wires them into the Plan
Compiler.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# AST nodes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StringLit:
    value: str


@dataclass(frozen=True)
class _BoolLit:
    value: bool


@dataclass(frozen=True)
class _Path:
    parts: tuple[str, ...]  # e.g. ('design-review', 'json', 'verdict')


@dataclass(frozen=True)
class _Eq:
    left: _Expr
    right: _Expr


@dataclass(frozen=True)
class _Neq:
    left: _Expr
    right: _Expr


@dataclass(frozen=True)
class _And:
    left: _Expr
    right: _Expr


@dataclass(frozen=True)
class _Or:
    left: _Expr
    right: _Expr


@dataclass(frozen=True)
class _Not:
    expr: _Expr


_Expr = _StringLit | _BoolLit | _Path | _Eq | _Neq | _And | _Or | _Not


class PredicateError(ValueError):
    """Raised on parse failure or evaluation against missing path."""


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


@dataclass
class _Token:
    kind: str  # 'STRING', 'BOOL', 'PATH', 'OP', 'KW', 'LPAREN', 'RPAREN', 'EOF'
    value: str
    pos: int


def _tokenize(src: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c.isspace():
            i += 1
            continue
        if c == "(":
            tokens.append(_Token("LPAREN", "(", i))
            i += 1
            continue
        if c == ")":
            tokens.append(_Token("RPAREN", ")", i))
            i += 1
            continue
        if c in {"'", '"'}:
            quote = c
            j = i + 1
            while j < n and src[j] != quote:
                if src[j] == "\\":
                    j += 2
                else:
                    j += 1
            if j >= n:
                raise PredicateError(f"unterminated string at pos {i}")
            tokens.append(_Token("STRING", src[i + 1 : j], i))
            i = j + 1
            continue
        if c == "=" and i + 1 < n and src[i + 1] == "=":
            tokens.append(_Token("OP", "==", i))
            i += 2
            continue
        if c == "!" and i + 1 < n and src[i + 1] == "=":
            tokens.append(_Token("OP", "!=", i))
            i += 2
            continue
        # Identifier/path/keyword/bool: [a-zA-Z_][a-zA-Z0-9_.\-]*
        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "._-"):
                j += 1
            word = src[i:j]
            if word == "true":
                tokens.append(_Token("BOOL", "true", i))
            elif word == "false":
                tokens.append(_Token("BOOL", "false", i))
            elif word in {"and", "or", "not"}:
                tokens.append(_Token("KW", word, i))
            else:
                tokens.append(_Token("PATH", word, i))
            i = j
            continue
        raise PredicateError(f"unexpected character {c!r} at pos {i}")
    tokens.append(_Token("EOF", "", n))
    return tokens


# ---------------------------------------------------------------------------
# Parser (recursive descent; precedence: not > and > or)
# ---------------------------------------------------------------------------


class _Parser:
    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> _Token:
        return self._tokens[self._pos]

    def _eat(self, kind: str, value: str | None = None) -> _Token:
        t = self._peek()
        if t.kind != kind or (value is not None and t.value != value):
            raise PredicateError(
                f"expected {kind}{(' ' + value) if value else ''}, got {t.kind} {t.value!r} at pos {t.pos}"
            )
        self._pos += 1
        return t

    def parse(self) -> _Expr:
        e = self._parse_or()
        if self._peek().kind != "EOF":
            t = self._peek()
            raise PredicateError(f"trailing tokens at pos {t.pos}: {t.value!r}")
        return e

    def _parse_or(self) -> _Expr:
        left = self._parse_and()
        while self._peek().kind == "KW" and self._peek().value == "or":
            self._pos += 1
            right = self._parse_and()
            left = _Or(left, right)
        return left

    def _parse_and(self) -> _Expr:
        left = self._parse_not()
        while self._peek().kind == "KW" and self._peek().value == "and":
            self._pos += 1
            right = self._parse_not()
            left = _And(left, right)
        return left

    def _parse_not(self) -> _Expr:
        if self._peek().kind == "KW" and self._peek().value == "not":
            self._pos += 1
            return _Not(self._parse_not())
        return self._parse_comparison()

    def _parse_comparison(self) -> _Expr:
        left = self._parse_atom()
        t = self._peek()
        if t.kind == "OP":
            self._pos += 1
            right = self._parse_atom()
            if t.value == "==":
                return _Eq(left, right)
            return _Neq(left, right)
        return left

    def _parse_atom(self) -> _Expr:
        t = self._peek()
        if t.kind == "LPAREN":
            self._pos += 1
            e = self._parse_or()
            self._eat("RPAREN")
            return e
        if t.kind == "STRING":
            self._pos += 1
            return _StringLit(t.value)
        if t.kind == "BOOL":
            self._pos += 1
            return _BoolLit(t.value == "true")
        if t.kind == "PATH":
            self._pos += 1
            return _Path(tuple(t.value.split(".")))
        raise PredicateError(f"unexpected token {t.kind} {t.value!r} at pos {t.pos}")


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


def _resolve_path(parts: tuple[str, ...], context: dict[str, Any]) -> Any:
    cur: Any = context
    for p in parts:
        if not isinstance(cur, dict):
            raise PredicateError(
                f"path traversal expected dict at segment {p!r}, got {type(cur).__name__}"
            )
        if p not in cur:
            raise PredicateError(f"path segment {p!r} not present in context")
        cur = cur[p]
    return cur


def _evaluate(expr: _Expr, context: dict[str, Any]) -> Any:
    if isinstance(expr, _StringLit):
        return expr.value
    if isinstance(expr, _BoolLit):
        return expr.value
    if isinstance(expr, _Path):
        return _resolve_path(expr.parts, context)
    if isinstance(expr, _Eq):
        return _evaluate(expr.left, context) == _evaluate(expr.right, context)
    if isinstance(expr, _Neq):
        return _evaluate(expr.left, context) != _evaluate(expr.right, context)
    if isinstance(expr, _And):
        return bool(_evaluate(expr.left, context)) and bool(_evaluate(expr.right, context))
    if isinstance(expr, _Or):
        return bool(_evaluate(expr.left, context)) or bool(_evaluate(expr.right, context))
    # Final variant: _Not (the union is exhausted by the branches above).
    return not bool(_evaluate(expr.expr, context))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_predicate(src: str) -> _Expr:
    """Parse a predicate source string into an AST.

    Raises :class:`PredicateError` on syntax errors. The returned AST is opaque
    to callers; pass it to :func:`evaluate_predicate` along with a context.
    """
    return _Parser(_tokenize(src)).parse()


def evaluate_predicate(src_or_ast: str | _Expr, context: dict[str, Any]) -> bool:
    """Parse (if needed) and evaluate a predicate against *context*.

    The *context* is a nested dict; dotted-path tokens in the predicate are
    resolved against it. Result is coerced to ``bool`` at the top level —
    intermediate results may be strings or booleans depending on the operator.
    """
    ast = src_or_ast if not isinstance(src_or_ast, str) else parse_predicate(src_or_ast)
    return bool(_evaluate(ast, context))
