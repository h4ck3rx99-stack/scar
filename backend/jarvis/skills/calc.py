"""Exact arithmetic for spoken maths ("what's 17 percent of 2340"): the LLM is bad at digits, Python isn't."""
import ast
import math
import operator
import re

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos, ast.FloorDiv: operator.floordiv}
_FUNCS = {"sqrt": math.sqrt, "sin": lambda x: math.sin(math.radians(x)), "cos": lambda x: math.cos(math.radians(x)),
          "tan": lambda x: math.tan(math.radians(x)), "log": math.log10, "ln": math.log, "abs": abs, "round": round}
_WORDS = [(r"\bplus\b", "+"), (r"\bminus\b", "-"), (r"\b(times|multiplied by|into|x)\b", "*"), (r"\b(divided by|over)\b", "/"),
          (r"\bsquared\b", "**2"), (r"\bcubed\b", "**3"), (r"\bto the power of\b", "**"), (r"\bsquare root of\b", "sqrt "),
          (r"(\d+(?:\.\d+)?)\s*(?:percent|%)\s*of\s*", r"\1/100*"), (r"\bpercent\b", "/100"), (r"\bpi\b", "3.141592653589793"),
          (r",", "")]


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and len(node.args) == 1:
        return _FUNCS[node.func.id](_eval(node.args[0]))
    raise ValueError("unsupported expression")


def evaluate(expr: str):
    e = expr.lower().strip().rstrip("?")
    for pat, rep in _WORDS:
        e = re.sub(pat, rep, e)
    e = re.sub(r"(sqrt|sin|cos|tan|log|ln)\s+([\d.]+)", r"\1(\2)", e)
    e = re.sub(r"[^\d+\-*/().%a-z\s]", "", e).strip()
    value = _eval(ast.parse(e, mode="eval").body)
    if isinstance(value, float):
        value = round(value, 6)
        if value.is_integer():
            value = int(value)
    return value


def spoken_number(v):
    if isinstance(v, float):
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    return f"{v:,}"
