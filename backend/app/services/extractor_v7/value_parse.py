"""Normalize performance values, especially scientific notation."""

from __future__ import annotations

import re
from typing import Any

# Unicode superscripts → ASCII (10⁻⁴ → 10-4)
_SUPERSCRIPT_MAP = str.maketrans({
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
    "⁻": "-", "⁺": "+",
})

# Mantissa × 10^exp
_SCI_ANCHOR_RE = re.compile(
    r"([+-]?\d+(?:\.\d+)?)\s*(?:×|x|\*|·)\s*10",
    re.I,
)
_SCI_E_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*[eE]\s*([+-]?\d+)", re.I)

# Evidence contains scientific notation markers
_SCI_EVIDENCE_HINT = re.compile(
    r"(?:"
    r"(?:×|x|\*|·)\s*10\s*[\^*\s]*[+-]?\d+|"
    r"10\s*[\^*\s]*[+-]?\d+|"
    r"10\s*[−\-]\s*\d+|"
    r"10[⁻⁰¹²³⁴⁵⁶⁷⁸⁹]+|"
    r"[eE]\s*[+-]?\d+"
    r")",
    re.I,
)


def normalize_scientific_text(text: str) -> str:
    """Normalize unicode minus, multiply sign, and superscript exponents."""
    raw = (text or "").strip()
    raw = raw.translate(_SUPERSCRIPT_MAP)
    raw = raw.replace("−", "-").replace("–", "-").replace("—", "-")
    raw = raw.replace("×", "x").replace("·", "*")
    return raw.replace(",", "")


def evidence_has_scientific_notation(evidence: str) -> bool:
    return bool(_SCI_EVIDENCE_HINT.search(normalize_scientific_text(evidence)))


def _parse_exponent_after_10(tail: str) -> int | None:
    """Parse exponent after literal '10' (handles 10^-4, 10-4, 10^4, 10⁻⁴)."""
    s = normalize_scientific_text(tail).lstrip()
    if not s:
        return None
    if s[0] in "^*":
        m = re.match(r"[\^*]+\s*(-?\d+)", s)
        return int(m.group(1)) if m else None
    if s[0] == "-":
        m = re.match(r"-\s*(\d+)", s)
        return -int(m.group(1)) if m else None
    m = re.match(r"(-?\d+)", s)
    return int(m.group(1)) if m else None


def _format_sci_mantissa_exp(mantissa: str, exp: int) -> str:
    if exp == 0:
        return mantissa
    exp_str = f"{exp:+d}".replace("+", "")
    base = mantissa.rstrip("0").rstrip(".") if "." in mantissa else mantissa
    return f"{base}e{exp_str}"


def parse_scientific_value(text: str) -> str | None:
    """Parse full numeric value including scientific notation."""
    raw = normalize_scientific_text(text)
    if not raw:
        return None

    anchor = _SCI_ANCHOR_RE.search(raw)
    if anchor:
        mantissa = anchor.group(1)
        tail = raw[anchor.end():]
        exp = _parse_exponent_after_10(tail)
        if exp is not None:
            return _format_sci_mantissa_exp(mantissa, exp)

    match = _SCI_E_RE.search(raw)
    if match:
        exp = int(match.group(2))
        return _format_sci_mantissa_exp(match.group(1), exp)

    plain = re.fullmatch(r"[+-]?\d+(?:\.\d+)?", raw)
    if plain:
        return raw
    return None


def iter_scientific_spans(evidence: str) -> list[tuple[str, str]]:
    """Return (mantissa, full_parsed_value) for each sci notation span in evidence."""
    ev = normalize_scientific_text(evidence)
    spans: list[tuple[str, str]] = []
    for anchor in _SCI_ANCHOR_RE.finditer(ev):
        mantissa = anchor.group(1)
        tail = ev[anchor.end():]
        exp = _parse_exponent_after_10(tail)
        if exp is None:
            continue
        full = _format_sci_mantissa_exp(mantissa, exp)
        spans.append((mantissa, full))
    for match in _SCI_E_RE.finditer(ev):
        full = _format_sci_mantissa_exp(match.group(1), int(match.group(2)))
        spans.append((match.group(1), full))
    return spans


def reconcile_value_with_evidence(value: Any, evidence: str) -> tuple[str, bool]:
    """If value is truncated mantissa, recover from evidence scientific notation."""
    text = str(value or "").strip().replace(",", "")
    if not evidence:
        return text, False

    for mantissa, full in iter_scientific_spans(evidence):
        if text == mantissa or text == mantissa.rstrip("0").rstrip("."):
            return full, True

    parsed = parse_scientific_value(text)
    if parsed and parsed != text:
        return parsed, True
    return text, False


def validate_scientific_notation(value: Any, evidence: str) -> tuple[str, bool]:
    """Return (corrected_value, is_valid). Auto-fix mantissa-only when evidence has sci."""
    text = str(value or "").strip()
    if not evidence_has_scientific_notation(evidence):
        return text, True

    fixed, changed = reconcile_value_with_evidence(text, evidence)
    if changed:
        return fixed, True

    for mantissa, full in iter_scientific_spans(evidence):
        if text == mantissa or text == mantissa.rstrip("0").rstrip("."):
            return full, True

    return text, True
