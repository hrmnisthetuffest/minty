"""The tutor's checker (579): arithmetic and school algebra evaluated exactly, so a number or a step is never taken
on the model's word alone. Exact fractions; one variable; equations up to degree 2. No eval, no imports of
user text: a small hand-written parser. (An own implementation: sympy is not installed.)"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from fractions import Fraction
from decimal import Decimal

MAX_TOKENS = 200
MAX_EXP = 12
MAX_DEGREE = 8
TOL = 1e-6


class VerifyError(ValueError):
    pass


# ---- polynomials in one variable ---------------------------------------------------------------------------
class Poly:
    __slots__ = ("c",)

    def __init__(self, c: dict[int, Fraction]):
        self.c = {d: v for d, v in c.items() if v != 0}

    @staticmethod
    def const(v) -> "Poly":
        return Poly({0: Fraction(v)})

    @property
    def degree(self) -> int:
        return max(self.c) if self.c else 0

    @property
    def is_const(self) -> bool:
        return all(d == 0 for d in self.c)

    def value(self) -> Fraction:
        return self.c.get(0, Fraction(0))

    def __add__(self, o):
        out = dict(self.c)
        for d, v in o.c.items():
            out[d] = out.get(d, Fraction(0)) + v
        return Poly(out)

    def __neg__(self):
        return Poly({d: -v for d, v in self.c.items()})

    def __sub__(self, o):
        return self + (-o)

    def __mul__(self, o):
        out: dict[int, Fraction] = {}
        for d1, v1 in self.c.items():
            for d2, v2 in o.c.items():
                out[d1 + d2] = out.get(d1 + d2, Fraction(0)) + v1 * v2
        if out and max(out) > MAX_DEGREE:
            raise VerifyError("that expression is too large to check")
        return Poly(out)

    def __eq__(self, o):
        return isinstance(o, Poly) and self.c == o.c


def _to_float(v) -> float:
    return float(v.value()) if isinstance(v, Poly) else float(v)


# ---- tokens and parser -------------------------------------------------------------------------------------
_TOKEN = re.compile(r"\s*(?:(?P<num>\d+(?:\.\d+)?)|(?P<name>[a-z]+)|(?P<op>\*\*|[-+*/^()×÷−]))")


def _tokens(text: str) -> list[tuple[str, str]]:
    s = text.lower().replace(",", "")
    s = re.sub(r"(\d+(?:\.\d+)?)\s*%\s*of\s+", r"(\1/100)*", s)
    s = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", s)
    out, pos = [], 0
    s = s.strip()
    while pos < len(s):
        m = _TOKEN.match(s, pos)
        if not m or m.end() == pos:
            raise VerifyError(f"I can't read {s[pos:pos + 12]!r} as maths")
        kind = m.lastgroup
        val = m.group(kind)
        if kind == "op":
            val = {"×": "*", "÷": "/", "−": "-", "**": "^"}.get(val, val)
        out.append((kind, val))
        pos = m.end()
        if len(out) > MAX_TOKENS:
            raise VerifyError("that is too long to check")
    return out


class _Parser:
    def __init__(self, tokens):
        self.t, self.i, self.var = tokens, 0, None

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def parse(self):
        v = self.expr()
        if self.peek()[0] is not None:
            raise VerifyError("there is something left over in that expression")
        return v

    def expr(self):
        v = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            r = self.term()
            v = _add(v, r) if op == "+" else _add(v, _neg(r))
        return v

    def starts_factor(self) -> bool:
        kind, val = self.peek()
        return kind in ("num", "name") or (kind == "op" and val == "(")

    def term(self):
        v = self.unary()
        while True:
            kind, val = self.peek()
            if (kind, val) in (("op", "*"), ("op", "/")):
                self.take()
                r = self.unary()
                v = _mul(v, r) if val == "*" else _div(v, r)
            elif self.starts_factor():  # implicit multiplication: 2x, 3(x+1), (a)(b)
                v = _mul(v, self.unary())
            else:
                return v

    def unary(self):
        if self.peek() == ("op", "-"):
            self.take()
            return _neg(self.unary())
        if self.peek() == ("op", "+"):
            self.take()
            return self.unary()
        return self.power()

    def power(self):
        base = self.atom()
        if self.peek() == ("op", "^"):
            self.take()
            exp = self.unary()
            return _pow(base, exp)
        return base

    def atom(self):
        kind, val = self.take()
        if kind == "num":
            return Poly.const(Fraction(Decimal(val)))
        if kind == "op" and val == "(":
            v = self.expr()
            if self.take() != ("op", ")"):
                raise VerifyError("a bracket is not closed")
            return v
        if kind == "name":
            if val in ("sqrt", "abs"):
                if self.take() != ("op", "("):
                    raise VerifyError(f"{val} needs brackets")
                inner = self.expr()
                if self.take() != ("op", ")"):
                    raise VerifyError("a bracket is not closed")
                return _func(val, inner)
            if len(val) != 1:
                raise VerifyError(f"I can only check numbers and one-letter unknowns, not {val!r}")
            if self.var not in (None, val):
                raise VerifyError("I can only check one unknown at a time")
            self.var = val
            return Poly({1: Fraction(1)})
        raise VerifyError("the expression ends too early")


def _add(a, b):
    if isinstance(a, Poly) and isinstance(b, Poly):
        return a + b
    if (isinstance(a, Poly) and not a.is_const) or (isinstance(b, Poly) and not b.is_const):
        raise VerifyError("I can't mix an unknown with an approximate number")
    return _to_float(a) + _to_float(b)


def _neg(a):
    return -a if isinstance(a, Poly) else -a


def _mul(a, b):
    if isinstance(a, Poly) and isinstance(b, Poly):
        return a * b
    if (isinstance(a, Poly) and not a.is_const) or (isinstance(b, Poly) and not b.is_const):
        raise VerifyError("I can't mix an unknown with an approximate number")
    return _to_float(a) * _to_float(b)


def _div(a, b):
    if isinstance(b, Poly):
        if not b.is_const:
            raise VerifyError("I can't divide by an unknown")
        if b.value() == 0:
            raise VerifyError("that divides by zero")
        if isinstance(a, Poly):
            return Poly({d: v / b.value() for d, v in a.c.items()})
        return a / float(b.value())
    if b == 0:
        raise VerifyError("that divides by zero")
    return _to_float(a) / b


def _pow(a, e):
    if not (isinstance(e, Poly) and e.is_const and e.value().denominator == 1):
        raise VerifyError("I can only check whole-number powers")
    n = int(e.value())
    if abs(n) > MAX_EXP:
        raise VerifyError("that power is too big to check")
    if isinstance(a, float):
        return a ** n
    if n < 0:
        if not a.is_const or a.value() == 0:
            raise VerifyError("I can't take a negative power of that")
        return Poly.const(Fraction(1) / a.value() ** -n)
    out = Poly.const(1)
    for _ in range(n):
        out = out * a
    return out


def _func(name: str, v):
    if isinstance(v, Poly) and not v.is_const:
        raise VerifyError(f"I can't take {name} of an unknown")
    x = v.value() if isinstance(v, Poly) else v
    if name == "abs":
        return Poly.const(abs(x)) if isinstance(v, Poly) else abs(x)
    if x < 0:
        raise VerifyError("that is the square root of a negative number")
    if isinstance(v, Poly):
        n, d = x.numerator, x.denominator
        rn, rd = math.isqrt(n), math.isqrt(d)
        if rn * rn == n and rd * rd == d:
            return Poly.const(Fraction(rn, rd))
    return math.sqrt(float(x))


# ---- public checks ----------------------------------------------------------------------------------------
def _parse(text: str):
    p = _Parser(_tokens(text))
    return p.parse(), p.var


def evaluate(text: str):
    """A Poly (exact) or a float (an irrational root was involved)."""
    value, _var = _parse(str(text))
    return value


def _same(a, b) -> bool:
    if isinstance(a, Poly) and isinstance(b, Poly):
        return a == b
    if (isinstance(a, Poly) and not a.is_const) or (isinstance(b, Poly) and not b.is_const):
        return False
    x, y = _to_float(a), _to_float(b)
    return abs(x - y) <= TOL * max(1.0, abs(x), abs(y))


def _show(v) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    if v.is_const:
        f = v.value()
        return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"
    parts = []
    for d in sorted(v.c, reverse=True):
        c = v.c[d]
        cs = str(c.numerator) if c.denominator == 1 else f"({c.numerator}/{c.denominator})"
        if d == 0:
            parts.append(cs)
        else:
            var = "x" if d == 1 else f"x^{d}"
            parts.append(var if c == 1 else f"-{var}" if c == -1 else f"{cs}{var}")
    return " + ".join(parts).replace("+ -", "- ") or "0"


def _solutions(equation: str):
    """(variable, values) where values are Fractions or floats; None means every value works; [] none."""
    if equation.count("=") != 1:
        raise VerifyError("that is not one equation")
    left, right = equation.split("=")
    (lv, lvar), (rv, rvar) = _parse(left), _parse(right)
    var = lvar or rvar or "x"
    if lvar and rvar and lvar != rvar:
        raise VerifyError("I can only check one unknown at a time")
    if not (isinstance(lv, Poly) and isinstance(rv, Poly)):
        raise VerifyError("I can only solve exact equations")
    p = lv - rv
    if p.degree > 2:
        raise VerifyError("I can check equations up to degree 2")
    a, b, c = p.c.get(2, Fraction(0)), p.c.get(1, Fraction(0)), p.c.get(0, Fraction(0))
    if a == 0 and b == 0:
        return var, (None if c == 0 else [])
    if a == 0:
        return var, [-c / b]
    d = b * b - 4 * a * c
    if d < 0:
        return var, []
    rn, rd = math.isqrt(d.numerator), math.isqrt(d.denominator)
    if rn * rn == d.numerator and rd * rd == d.denominator:
        s = Fraction(rn, rd)
        return var, sorted({(-b - s) / (2 * a), (-b + s) / (2 * a)})
    return var, sorted({(-float(b) - math.sqrt(float(d))) / (2 * float(a)),
                        (-float(b) + math.sqrt(float(d))) / (2 * float(a))})


def solve(equation: str) -> list[str]:
    """Exact solutions of a one-unknown equation up to degree 2, as 'x = ...'. [] means none (or none real);
    a first item starting 'every' means it holds for every value."""
    var, values = _solutions(equation)
    if values is None:
        return [f"every value of {var} works"]
    return [f"{var} = {_frac(v)}" if isinstance(v, Fraction) else f"{var} = {v:.6g}" for v in values]


def _frac(f: Fraction) -> str:
    return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"


@dataclass
class Verdict:
    ok: bool | None  # True right, False wrong, None could not be checked
    expected: str = ""
    message: str = ""
    statement: str = ""


def _numbers_in(claim: str) -> list[float]:
    parts = re.split(r"\bor\b|\band\b|[,;]", claim.lower())
    nums = []
    for part in parts:
        part = re.sub(r"^\s*[a-z]\s*=\s*", "", part.strip())
        if part:
            nums.append(_to_float(evaluate(part)))
    return nums


def check(statement: str, claimed: str) -> Verdict:
    try:
        if "=" in statement:
            _var, values = _solutions(statement)
            sols = solve(statement)
            want = [float(v) for v in (values or [])]
            text = ", ".join(sols) if sols else "no real solutions"
            low = claimed.lower().strip()
            if not sols:
                ok = bool(re.search(r"\bno (?:real )?solutions?\b|\bnone\b|\bnot possible\b", low))
            elif values is None:
                ok = bool(re.search(r"\bevery|\ball\b|\balways\b|\binfinite", low))
            else:
                try:
                    got = sorted(_numbers_in(claimed))
                except VerifyError:
                    return Verdict(None, text, "I could not verify that answer.")
                ok = len(got) == len(want) and all(abs(g - w) <= TOL * max(1, abs(w)) for g, w in zip(got, sorted(want)))
            return Verdict(ok, text, "That checks out." if ok else f"That is not right: the solution is {text}.")
        actual = evaluate(statement)
        claim = evaluate(claimed)
        ok = _same(actual, claim)
        shown = _show(actual)
        return Verdict(ok, shown, "That checks out." if ok else f"That is not right: it works out to {shown}.")
    except VerifyError as e:
        return Verdict(None, "", f"I could not verify that: {e}.")
    except (ValueError, ZeroDivisionError, OverflowError):
        return Verdict(None, "", "I could not verify that.")


_STATEMENT = re.compile(r"(?P<lhs>[\d\s.,+\-*/×÷^()%−]*\d[\d\s.,+\-*/×÷^()%−]*?)\s*=\s*(?P<rhs>−?-?\d[\d,]*(?:\.\d+)?)")


def verify_text(text: str) -> list[Verdict]:
    """Every plain arithmetic statement in a text ("56 + 6 = 63"); the wrong ones, as flags."""
    flags = []
    for m in _STATEMENT.finditer(str(text)):
        lhs = m.group("lhs").strip(" .,;")
        rhs = m.group("rhs").replace(",", "").rstrip(".,")
        if not re.match(r"[-−(]?\s*\d|\(", lhs):
            continue  # "2x + 3 = 11": it is an equation with an unknown, not a plain sum
        if not re.search(r"[+\-*/×÷^%−]", lhs.lstrip("-−")):
            continue  # "pi = 3.14": nothing to compute
        v = check(lhs, rhs)
        if v.ok is False:
            v.statement = f"{lhs} = {rhs}"
            flags.append(v)
    return flags
