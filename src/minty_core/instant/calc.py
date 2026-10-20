"""Maths said out loud: sums, percentages, tips and discounts, roots, primes, number bases, shapes. No model needed."""
from __future__ import annotations

import ast
import math
import operator
import re

from minty_core.instant.base import Query, Rule
from minty_core.instant.text import num

CATEGORY = "maths"
N = r"(-?\d+(?:\.\d+)?)"
P = r"(\d+(?:\.\d+)?)"  # a number that is not negative


def val(text: str) -> float:
    x = float(text)
    return int(x) if x.is_integer() and abs(x) < 1e15 else x


class TooBig(Exception):
    pass


# ---------------------------------------------------------------------------------------------- a safe calculator
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv}


def _power(a: float, b: float) -> float:
    if abs(b) > 1000 or (isinstance(a, int) and isinstance(b, int) and b >= 0 and a not in (0, 1, -1)
                         and b * math.log2(abs(a)) > 200):
        raise TooBig
    try:
        out = a ** b
    except OverflowError:
        raise TooBig from None
    if isinstance(out, complex):
        raise TooBig
    return out


def _eval(node: ast.AST):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _eval(node.operand)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow):
            return _power(a, b)
        if type(node.op) in _BIN:
            return _BIN[type(node.op)](a, b)
    raise ValueError("not arithmetic")


def evaluate(expression: str):
    """The value of a plain arithmetic expression, or raises (ValueError for anything else, TooBig, ZeroDivisionError)."""
    if len(expression) > 120:
        raise ValueError("too long")
    value = _eval(ast.parse(expression.strip(), mode="eval"))
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = int(value)
    return value


_LEAD = re.compile(r"^(?:what is|what are|calculate|compute|solve|work out|how much is|how many is|what does|math|equals?)\s+")
_TAIL = re.compile(r"\s+(?:equals?|is equal to|equal)$|\s*=$")
_WORD_OPS = [
    (r"\bto the power of\b|\bto the power\b|\braised to the power of\b|\braised to\b|\bpower\b", " ** "),
    (r"\bplus\b", " + "), (r"\bminus\b|\bless\b|\btake away\b", " - "),
    (r"\bmultiplied by\b|\btimes\b", " * "), (r"\bdivided by\b|\bover\b", " / "),
    (r"\bmodulo\b|\bmod\b", " % "), (r"\bsquared\b", " ** 2 "), (r"\bcubed\b", " ** 3 "),
]
_SPOKEN = {"+": "plus", "-": "minus", "*": "times", "/": "divided by", "**": "to the power of", "%": "mod"}
_TOKEN = re.compile(r"\d+(?:\.\d+)?|\*\*|[+\-*/%()]")


def expression(q: Query) -> str | None:
    s = q.digits.replace("×", " * ").replace("÷", " / ").replace("−", " - ")
    lead = _LEAD.match(s)
    if lead:
        s = s[lead.end():]
    s = _TAIL.sub("", s)
    m = re.match(r"^(add|subtract|multiply|divide) " + P + r" (?:and|to|from|by|with) " + P + r"$", s)
    if m:  # imperative: "add 5 and 3", "subtract 3 from 10", "multiply 6 by 7", "divide 20 by 4"
        a, b = m[2], m[3]
        s = {"add": f"{a} + {b}", "subtract": f"{b} - {a}", "multiply": f"{a} * {b}", "divide": f"{a} / {b}"}[m[1]]
        lead = lead or m
    spoken = False
    for pattern, symbol in _WORD_OPS:
        s, n = re.subn(pattern, symbol, s)
        spoken = spoken or n > 0
    s = re.sub(r"(?<=\d) x (?=[\d(])", " * ", s)
    s = re.sub(r"\^", " ** ", s)
    s = " ".join(s.split())
    if not re.fullmatch(r"[\d\s+\-*/().%]+", s) or not re.search(r"\d", s):
        return None
    if not (lead or spoken or re.search(r"[+*/%]", q.digits)):
        return None
    tokens = _TOKEN.findall(s)
    if sum(t[0].isdigit() for t in tokens) < (1 if "**" in s and "2" in s else 2):
        return None
    try:
        value = evaluate(s)
    except ZeroDivisionError:
        return "You can't divide by zero."
    except TooBig:
        return "That number is far too big to say."
    except (ValueError, SyntaxError, RecursionError, MemoryError):
        return None
    numbers = sum(t[0].isdigit() for t in tokens)
    if "(" in tokens or numbers > 4:
        return f"That's {num(value)}."
    words = " ".join(num(float(t)) if t[0].isdigit() else _SPOKEN.get(t, t) for t in tokens)
    words = words.replace("to the power of 2", "squared").replace("to the power of 3", "cubed")
    return f"{words} is {num(value)}."


# ---------------------------------------------------------------------------------------------- percentages and money
def _money(x: float) -> str:
    return num(round(x, 2))


def percentages(q: Query) -> str | None:
    d = q.digits
    m = re.fullmatch(rf"(?:what is )?{P} percent of {P}", d)
    if m:
        a, b = val(m[1]), val(m[2])
        return f"{num(a)} percent of {num(b)} is {num(a * b / 100)}."
    for pattern, order in ((rf"what percent(?:age)? of {P} is {P}", "total part"), (rf"what percent(?:age)? is {P} of {P}", "part total"),
                           (rf"{P} is what percent(?:age)? of {P}", "part total")):
        m = re.fullmatch(pattern, d)
        if m:
            vals = dict(zip(order.split(), (val(m[1]), val(m[2]))))
            if vals["total"] == 0:
                return "A percentage of zero doesn't mean anything."
            return f"{num(vals['part'])} is {num(vals['part'] / vals['total'] * 100)} percent of {num(vals['total'])}."
    m = re.fullmatch(rf"(?:increase|raise|add|up) {P} by {P} percent", d)
    if m:
        x, y = val(m[1]), val(m[2])
        return f"{num(x)} plus {num(y)} percent is {num(x * (1 + y / 100))}."
    m = re.fullmatch(rf"(?:decrease|reduce|cut|lower|drop) {P} by {P} percent", d)
    if m:
        x, y = val(m[1]), val(m[2])
        return f"{num(x)} minus {num(y)} percent is {num(x * (1 - y / 100))}."
    m = re.fullmatch(rf"add {P} percent to {P}", d)
    if m:
        x, y = val(m[2]), val(m[1])
        return f"{num(x)} plus {num(y)} percent is {num(x * (1 + y / 100))}."
    m = re.fullmatch(rf"(?:subtract|take|remove) {P} percent (?:from|off) {P}", d)
    if m:
        x, y = val(m[2]), val(m[1])
        return f"{num(x)} minus {num(y)} percent is {num(x * (1 - y / 100))}."
    for pattern in (rf"{P} percent (?:off|discount)(?: on| of| from)? {P}", rf"(?:a )?{P} percent discount (?:on|of) {P}",
                    rf"{P} percent off of {P}"):
        m = re.fullmatch(r"(?:what is |calculate )?" + pattern, d)
        if m:
            y, x = val(m[1]), val(m[2])
            return f"{num(y)} percent off {num(x)} is {_money(x * (1 - y / 100))}, so you save {_money(x * y / 100)}."
    m = re.fullmatch(rf"{P} with {P} percent (?:off|discount)", d)
    if m:
        x, y = val(m[1]), val(m[2])
        return f"{num(y)} percent off {num(x)} is {_money(x * (1 - y / 100))}, so you save {_money(x * y / 100)}."
    for pattern, ys, xs in ((rf"(?:a |the )?{P} percent tip (?:on|of|for) {P}", 1, 2), (rf"tip {P} percent (?:on|of|for) {P}", 1, 2),
                            (rf"(?:how much is |what is )?(?:the )?tip (?:on|of|for) {P}(?: at| with)? {P} percent", 2, 1)):
        m = re.fullmatch(pattern, d)
        if m:
            y, x = val(m[ys]), val(m[xs])
            tip = x * y / 100
            return f"A {num(y)} percent tip on {_money(x)} is {_money(tip)}, so {_money(x + tip)} in total."
    for pattern, ys, xs, ts in ((rf"(?:add )?{P} percent (gst|vat|tax|sales tax) (?:on|to|of) {P}", 1, 3, 2),
                                (rf"(gst|vat|tax|sales tax) {P} percent (?:on|to|of) {P}", 2, 3, 1),
                                (rf"(?:add )?(gst|vat|tax|sales tax) to {P} at {P} percent", 3, 2, 1)):
        m = re.fullmatch(pattern, d)
        if m:
            y, x, kind = val(m[ys]), val(m[xs]), m[ts].upper() if m[ts] in ("gst", "vat") else m[ts]
            tax = x * y / 100
            return f"{num(y)} percent {kind} on {_money(x)} is {_money(tax)}, so {_money(x + tax)} in total."
    for pattern in (rf"split (?:the )?(?:bill |cost )?(?:of )?{P} (?:between|among|by|with) (\d+)(?: people| persons| friends| ways| of us)?",
                    rf"(?:split|divide) {P} (\d+) ways"):
        m = re.fullmatch(pattern, d)
        if m:
            x, n = val(m[1]), int(m[2])
            if n == 0:
                return "You can't split something between nobody."
            return f"{_money(x)} split {n} ways is {_money(x / n)} each."
    return None


# ---------------------------------------------------------------------------------------------- one-step maths
_FRACTIONS = {"half": 0.5, "a half": 0.5, "a third": 1 / 3, "third": 1 / 3, "a quarter": 0.25, "quarter": 0.25, "a fifth": 0.2,
              "a tenth": 0.1, "tenth": 0.1, "double": 2, "twice": 2, "triple": 3}


def _fact(n: int) -> int:
    return math.factorial(n)


def one_step(q: Query) -> str | None:
    d = q.digits
    m = re.fullmatch(rf"(?:the )?square root of {P}", d) or re.fullmatch(rf"(?:the )?sqrt(?: of)? {P}", d)
    if m:
        x = val(m[1])
        r = math.sqrt(x)
        return f"The square root of {num(x)} is {num(round(r, 6) if not r.is_integer() else int(r))}."
    m = re.fullmatch(rf"(?:the )?cube root of {N}", d)
    if m:
        x = val(m[1])
        r = math.copysign(abs(x) ** (1 / 3), x)
        nearest = round(r)
        r = nearest if nearest ** 3 == x else r
        return f"The cube root of {num(x)} is {num(r)}."
    m = re.fullmatch(r"(?:what is )?(\d+) factorial|(?:the )?factorial of (\d+)", d)
    if m:
        n = int(m[1] or m[2])
        if n > 170:
            return "That factorial is far too big to say."
        return f"{n} factorial is {num(float(_fact(n)) if n > 20 else _fact(n))}."
    m = re.fullmatch(rf"(?:the )?(?:absolute value|abs) of {N}", d)
    if m:
        return f"The absolute value of {num(val(m[1]))} is {num(abs(val(m[1])))}."
    m = re.fullmatch(rf"round {N}(?: to (\d+) (?:decimal places?|decimals?|places?|dp))?", d)
    if m:
        x, places = val(m[1]), int(m[2] or 0)
        return f"{num(x)} rounded is {num(round(x, places))}."
    m = re.fullmatch(rf"round (up|down) {N}", d)
    if m:
        x = val(m[2])
        return f"{num(x)} rounded {m[1]} is {num(math.ceil(x) if m[1] == 'up' else math.floor(x))}."
    m = re.fullmatch(rf"round {N} to the nearest (ten|hundred|thousand|\d+)", d)
    if m:
        step = {"ten": 10, "hundred": 100, "thousand": 1000}.get(m[2]) or int(m[2])
        if step:
            return f"{num(val(m[1]))} to the nearest {step} is {num(round(val(m[1]) / step) * step)}."
    m = re.fullmatch(rf"(half|a half|a third|third|a quarter|quarter|a fifth|a tenth|tenth) of {N}", d)
    if m:
        x = val(m[2])
        return f"{m[1].capitalize()} of {num(x)} is {num(x * _FRACTIONS[m[1]])}."
    m = re.fullmatch(rf"(double|triple|twice|halve) {N}", d)
    if m:
        x = val(m[2])
        return f"{m[1].capitalize()} {num(x)} is {num(x / 2 if m[1] == 'halve' else x * _FRACTIONS[m[1]])}."
    m = re.fullmatch(rf"(?:the )?(?:log|logarithm)(?: base {P})? of {P}", d)
    plain = re.fullmatch(rf"(?:the )?(?:log|logarithm) {P}", d)
    if m or plain:
        base, x = (m[1], val(m[2])) if m else (None, val(plain[1]))
        if x <= 0:
            return "You can only take the log of a positive number."
        b = val(base) if base else 10
        if b <= 0 or b == 1:
            return None
        return f"The log of {num(x)} in base {num(b)} is {num(round(math.log(x, b), 6))}."
    m = re.fullmatch(rf"(?:the )?(?:natural log|ln)(?: of)? {P}", d)
    if m:
        x = val(m[1])
        if x <= 0:
            return "You can only take the log of a positive number."
        return f"The natural log of {num(x)} is {num(round(math.log(x), 6))}."
    m = re.fullmatch(rf"(sin|sine|cos|cosine|tan|tangent)(?: of)? {N}(?: degrees?)?", d)
    if m:
        name = {"sin": "sine", "sine": "sine", "cos": "cosine", "cosine": "cosine", "tan": "tangent", "tangent": "tangent"}[m[1]]
        fn = {"sine": math.sin, "cosine": math.cos, "tangent": math.tan}[name]
        x = val(m[2])
        if name == "tangent" and abs((x - 90) % 180) < 1e-9:
            return "The tangent of that angle is undefined."
        return f"The {name} of {num(x)} degrees is {num(round(fn(math.radians(x)), 6) + 0.0)}."
    if re.fullmatch(r"(?:the )?(?:value of )?pi", d) or d in ("what is pi", "pi value"):
        return "Pi is about 3.14159265."
    if re.fullmatch(r"(?:the )?(?:value of )?(?:euler's number|eulers number)", d):
        return "Euler's number is about 2.71828183."
    if re.fullmatch(r"(?:the )?(?:value of )?(?:the )?golden ratio", d):
        return "The golden ratio is about 1.61803399."
    return None


# ---------------------------------------------------------------------------------------------- whole numbers
def _prime(n: int) -> int | None:
    """None if n is prime, else its smallest factor."""
    if n < 2:
        return n if n else 1
    for f in (2, 3):
        if n % f == 0:
            return None if n == f else f
    f = 5
    while f * f <= n:
        if n % f == 0 or n % (f + 2) == 0:
            return f if n % f == 0 else f + 2
        f += 6
    return None


def _factors(n: int) -> list[int]:
    small = [i for i in range(1, int(math.isqrt(n)) + 1) if n % i == 0]
    return sorted(set(small + [n // i for i in small]))


def _prime_factors(n: int) -> list[int]:
    out, f = [], 2
    while f * f <= n:
        while n % f == 0:
            out.append(f)
            n //= f
        f += 1 if f == 2 else 2
    if n > 1:
        out.append(n)
    return out


def _list(items) -> str:
    items = [str(i) for i in items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def whole_numbers(q: Query) -> str | None:
    d = q.digits
    m = re.fullmatch(r"is (\d+) (?:a )?prime(?: number)?", d)
    if m:
        n = int(m[1])
        if n > 10**12:
            return None
        factor = _prime(n)
        if factor is None:
            return f"Yes, {n} is prime."
        if n < 2:
            return f"No, {n} is not prime."
        return f"No, {n} is not prime. It divides by {factor}."
    m = re.fullmatch(r"(?:what are )?(?:the )?factors of (\d+)", d)
    if m:
        n = int(m[1])
        if not 0 < n <= 10**9:
            return None
        found = _factors(n)
        if len(found) > 30:
            return f"{n} has {len(found)} factors."
        return f"The factors of {n} are {_list(found)}."
    m = re.fullmatch(r"(?:what are )?(?:the )?prime factors of (\d+)|(?:the )?prime factori[sz]ation of (\d+)", d)
    if m:
        n = int(m[1] or m[2])
        if not 1 < n <= 10**12:
            return None
        return f"{n} is {' times '.join(map(str, _prime_factors(n)))}." if _prime(n) is not None else f"{n} is prime."
    m = re.fullmatch(r"(?:the )?(gcd|hcf|greatest common (?:divisor|factor)|highest common factor|lcm|least common multiple|"
                     r"lowest common multiple) of (\d+)(?: and)? (\d+)", d)
    if m:
        a, b = int(m[2]), int(m[3])
        if m[1] in ("gcd", "hcf") or m[1].startswith(("greatest", "highest")):
            return f"The greatest common divisor of {a} and {b} is {math.gcd(a, b)}."
        return f"The least common multiple of {a} and {b} is {num(math.lcm(a, b))}."
    m = re.fullmatch(r"is (\d+) (?:an? )?(even|odd)(?: number)?", d)
    if m:
        n = int(m[1])
        is_even = n % 2 == 0
        return f"{'Yes' if is_even == (m[2] == 'even') else 'No'}, {n} is {'even' if is_even else 'odd'}."
    return None


_STATS = {"average": "average", "mean": "average", "sum": "sum", "total": "sum", "product": "product", "maximum": "biggest",
          "max": "biggest", "biggest": "biggest", "largest": "biggest", "minimum": "smallest", "min": "smallest",
          "smallest": "smallest", "median": "median", "range": "range"}


def statistics(q: Query) -> str | None:
    m = re.fullmatch(r"(?:the )?(average|mean|sum|total|product|maximum|max|biggest|largest|minimum|min|smallest|median|range)"
                     r"(?: number)? of ((?:-?\d+(?:\.\d+)?(?: and |, | )?)+)", q.digits.replace(",", " "))
    if not m:
        return None
    values = [val(x) for x in re.findall(r"-?\d+(?:\.\d+)?", m[2])]
    if len(values) < 2 or len(values) > 40:
        return None
    kind = _STATS[m[1]]
    result = {"average": sum(values) / len(values), "sum": sum(values), "product": math.prod(values), "biggest": max(values),
              "smallest": min(values), "range": max(values) - min(values),
              "median": sorted(values)[len(values) // 2] if len(values) % 2 else sum(sorted(values)[len(values) // 2 - 1:len(values) // 2 + 1]) / 2}[kind]
    return f"The {kind} of {_list(num(v) for v in values)} is {num(result)}."


# ---------------------------------------------------------------------------------------------- bases, roman, tables
_ROMAN = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"),
          (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def to_roman(n: int) -> str:
    out = ""
    for value, symbol in _ROMAN:
        while n >= value:
            out, n = out + symbol, n - value
    return out


def from_roman(text: str) -> int | None:
    total, rest = 0, text.upper()
    for value, symbol in _ROMAN:
        while rest.startswith(symbol):
            total, rest = total + value, rest[len(symbol):]
    return total if not rest and total and to_roman(total) == text.upper() else None


_BASES = {"binary": (2, "binary", "b"), "hex": (16, "hexadecimal", "X"), "hexadecimal": (16, "hexadecimal", "X"),
          "octal": (8, "octal", "o")}


def number_systems(q: Query) -> str | None:
    d = q.digits
    for pattern in (r"(?:convert )?(\d+) (?:to|in|into|as) (binary|hex|hexadecimal|octal)",
                    r"(?:the )?(binary|hex|hexadecimal|octal)(?: value)? (?:of|for) (\d+)"):
        m = re.fullmatch(pattern, d)
        if m:
            n, base = (int(m[1]), m[2]) if m[1].isdigit() else (int(m[2]), m[1])
            if n >= 2**64:
                return None
            _, name, code = _BASES[base]
            return f"{n} in {name} is {format(n, code)}."
    m = re.fullmatch(r"(?:convert |what is )?(?:binary )?([01]{2,})(?: binary)? (?:to|in|into) decimal", d)
    if m:
        return f"Binary {m[1]} is {int(m[1], 2)} in decimal."
    m = re.fullmatch(r"(?:convert |what is )?(?:hex|hexadecimal) ([0-9a-f]+) (?:to|in|into) decimal", d)
    if m:
        return f"Hexadecimal {m[1].upper()} is {int(m[1], 16)} in decimal."
    m = re.fullmatch(r"(?:what is |the )?roman numerals? (?:for|of) (\d+)", d)
    if m:
        n = int(m[1])
        return f"{n} in Roman numerals is {to_roman(n)}." if 0 < n < 4000 else "Roman numerals only go up to 3999."
    m = re.fullmatch(r"(?:what is |convert )?([ivxlcdm]+) (?:in|as|to) (?:numbers|arabic|decimal|digits|numerals)|"
                     r"what number is ([ivxlcdm]+)", d)
    if m:
        text = m[1] or m[2]
        n = from_roman(text)
        return f"{text.upper()} is {n}." if n else None
    m = re.fullmatch(r"(?:the )?(\d+) times table|(?:read |say |recite )?(?:the )?table of (\d+)|(\d+) table", d)
    if m:
        n = int(m[1] or m[2] or m[3])
        if n > 1000:
            return None
        return f"The {n} times table: " + ", ".join(str(n * i) for i in range(1, 11)) + "."
    return None


# ---------------------------------------------------------------------------------------------- shapes
def shapes(q: Query) -> str | None:
    d = q.digits
    m = re.fullmatch(rf"(area|circumference|perimeter) of (?:a )?circle(?: with)?(?: a)? (radius|diameter)(?: of)? {P}", d)
    if m:
        r = val(m[3]) / (2 if m[2] == "diameter" else 1)
        if m[1] == "area":
            return f"The area of a circle with {m[2]} {num(val(m[3]))} is {num(round(math.pi * r * r, 6))}."
        return f"The circumference of a circle with {m[2]} {num(val(m[3]))} is {num(round(2 * math.pi * r, 6))}."
    m = re.fullmatch(rf"(area|perimeter) of (?:a )?(?:rectangle|square|rectangle with sides|rectangle that is) ?{P} (?:by|and|times|x) {P}", d)
    if m:
        a, b = val(m[2]), val(m[3])
        return f"The {m[1]} of a {num(a)} by {num(b)} rectangle is {num(a * b if m[1] == 'area' else 2 * (a + b))}."
    m = re.fullmatch(rf"(area|perimeter) of (?:a )?square(?: with)?(?: a)?(?: side| sides| side of)? {P}", d)
    if m:
        a = val(m[2])
        return f"The {m[1]} of a square with side {num(a)} is {num(a * a if m[1] == 'area' else 4 * a)}."
    m = re.fullmatch(rf"area of (?:a )?triangle(?: with)? base(?: of)? {P}(?: and)? height(?: of)? {P}", d)
    if m:
        return f"The area of that triangle is {num(val(m[1]) * val(m[2]) / 2)}."
    m = re.fullmatch(rf"volume of (?:a )?sphere(?: with)?(?: a)? radius(?: of)? {P}", d)
    if m:
        r = val(m[1])
        return f"The volume of a sphere with radius {num(r)} is {num(round(4 / 3 * math.pi * r ** 3, 6))}."
    m = re.fullmatch(rf"volume of (?:a )?cube(?: with)?(?: a)?(?: side| edge)?(?: of)? {P}", d)
    if m:
        a = val(m[1])
        return f"The volume of a cube with side {num(a)} is {num(a ** 3)}."
    m = re.fullmatch(rf"volume of (?:a )?cylinder(?: with)?(?: a)? radius(?: of)? {P}(?: and)? height(?: of)? {P}", d)
    if m:
        r, h = val(m[1]), val(m[2])
        return f"The volume of that cylinder is {num(round(math.pi * r * r * h, 6))}."
    m = re.fullmatch(rf"(?:what is )?(?:the )?hypotenuse of (?:a )?(?:right )?(?:triangle )?(?:with )?(?:sides )?{P} (?:and|by) {P}", d)
    if m:
        return f"The hypotenuse is {num(round(math.hypot(val(m[1]), val(m[2])), 6))}."
    return None


RULES = (
    Rule("percentages", CATEGORY, ("what's 15 percent of 240", "20% of 150", "what percent of 80 is 20", "30 is what percent of 120",
                                   "increase 200 by 10 percent", "reduce 80 by 25 percent", "20 percent off 1500",
                                   "15 percent tip on 840", "18 percent gst on 1000", "split 1200 between 4 people",
                                   "add 10 percent to 50", "subtract 20 percent from 400", "what is 5 percent of two hundred"),
         percentages),
    Rule("one-step maths", CATEGORY, ("square root of 144", "cube root of 27", "5 factorial", "absolute value of -7", "round 3.14159 to 2 decimals",
                                      "round up 3.2", "half of 50", "double 16", "a third of 90", "log of 100", "natural log of 10",
                                      "sine of 30 degrees", "what is pi", "round 1234 to the nearest hundred", "triple 7",
                                      "log base 2 of 8", "the golden ratio"), one_step),
    Rule("whole numbers", CATEGORY, ("is 17 prime", "factors of 36", "prime factors of 84", "gcd of 12 and 18", "lcm of 4 and 6",
                                     "is 12 even", "greatest common divisor of 48 and 36", "is 91 a prime number"), whole_numbers),
    Rule("statistics", CATEGORY, ("average of 4, 8 and 15", "sum of 3 5 7", "the maximum of 4 9 2", "median of 1, 5, 9",
                                  "product of 2, 3 and 4", "range of 3 8 20"), statistics),
    Rule("number systems", CATEGORY, ("255 in binary", "convert 10 to binary", "hex of 255", "binary 1010 to decimal",
                                      "roman numeral for 1994", "what is XIV in numbers", "7 times table", "octal of 64",
                                      "hexadecimal ff to decimal"), number_systems),
    Rule("shapes", CATEGORY, ("area of a circle with radius 7", "circumference of a circle with diameter 10",
                              "area of a rectangle 5 by 8", "perimeter of a rectangle 5 by 8", "area of a triangle base 6 height 4",
                              "volume of a sphere with radius 3", "volume of a cube with side 4", "hypotenuse of 3 and 4",
                              "volume of a cylinder with radius 2 and height 5", "area of a square with side 6"), shapes),
    Rule("arithmetic", CATEGORY, ("what is 12 plus 7", "144 divided by 12", "what's 9 times 8", "twelve minus five", "2 to the power of 10",
                                  "7 squared", "what is (2 + 3) * 4", "100 / 8", "15 mod 4", "5+5", "multiply 6 by 7",
                                  "add 5 and 3", "what is 3 cubed", "10 minus 4 times 2", "calculate 99 times 99"), expression),
)
