"""Indian number formatting.

Every rupee figure Atlas prints is read by someone who thinks in lakhs and
crores. `f"{950000:,}"` renders `950,000`, which an Indian reader has to stop
and re-parse; the same number written `9,50,000` reads as "nine and a half
lakh" at a glance. On a briefing whose whole job is a fast morning scan, that
is not cosmetic — a misread ceiling is a misread decision.

The convention: the last three digits group as one, everything above them
groups in twos.

    950000     ->        9,50,000   (9.5 lakh)
    9549795    ->       95,49,795   (95.5 lakh)
    27000000   ->     2,70,00,000   (2.7 crore)
"""


def inr(value: float | int | None, dash: str = "-") -> str:
    """Group digits the Indian way. `None` renders as `dash`."""
    if value is None:
        return dash
    n = int(round(float(value)))
    sign = "-" if n < 0 else ""
    digits = str(abs(n))
    if len(digits) <= 3:
        return sign + digits

    head, tail = digits[:-3], digits[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return f"{sign}{','.join(groups)},{tail}"


def rs(value: float | int | None, dash: str = "-") -> str:
    """`inr` with the currency prefix — the common case in reports."""
    return dash if value is None else f"Rs {inr(value)}"


def compact(value: float | int | None, dash: str = "-") -> str:
    """Short form for dense columns: `95.5L`, `2.7Cr`.

    Only for places where the exact rupee is not the point — a ladder or a
    ranked table, where the reader wants magnitude at a glance. Anything that
    is a *decision* figure (a cash bar, a ceiling) keeps its full digits.
    """
    if value is None:
        return dash
    n = float(value)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1e7:
        return f"{sign}{n / 1e7:.2f}".rstrip("0").rstrip(".") + "Cr"
    if n >= 1e5:
        return f"{sign}{n / 1e5:.2f}".rstrip("0").rstrip(".") + "L"
    return sign + inr(n)
