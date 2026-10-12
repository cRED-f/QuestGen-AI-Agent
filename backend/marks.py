"""Fixed-point arithmetic for exam mark allocations (hundredths of a mark)."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


def to_cents(value: int | float | str, *, max_marks: int = 100) -> int:
    """Validate a mark value and convert exactly to integer hundredths.

    Floats arriving from Pydantic JSON are converted via str, so 0.1 remains
    one tenth instead of its binary floating-point approximation.
    """
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('Marks must be a valid number') from exc
    if not amount.is_finite() or amount < Decimal('0.01') or amount > max_marks:
        raise ValueError(f'Marks must be between 0.01 and {max_marks}')
    units = amount * 100
    if units != units.to_integral_value():
        raise ValueError('Marks can use at most two decimal places')
    return int(units)


def allocated_cents(sections: list[dict]) -> int:
    return sum(section['count'] * to_cents(section['marks_each']) for section in sections)


def display(value: int | float) -> str:
    """Present whole marks without .0 while retaining fractional marks."""
    return format(Decimal(str(value)).normalize(), 'f')
