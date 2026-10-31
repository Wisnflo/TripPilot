"""Decimal currency arithmetic; JSON numbers are emitted only at the boundary."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal("0.01")


def decimal(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not an amount")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("invalid amount") from exc
    if not result.is_finite():
        raise ValueError("amount must be finite")
    return result


def amount(value):
    return decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def number(value):
    return float(amount(value))


def multiply(price, count):
    return number(decimal(price) * decimal(count))


def sum_cost(items):
    return sum((amount(item.get("cost", 0)) for item in items), Decimal(0))


def plan_total(plan):
    activities = [a for day in plan.get("itinerary", []) for a in day.get("activities", [])]
    return sum_cost(activities) + sum((sum_cost(a.get("transports", [])) for a in activities), Decimal(0))
