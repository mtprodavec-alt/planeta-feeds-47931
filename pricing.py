"""Minimum whole-UAH selling price for one Planeta item.

Commission bands are supplied for one verified category and brand as
(inclusive lower bound, exclusive upper bound or None, effective percent).
"""
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from hashlib import sha256


def selling_price(wholesale, current, commission_bands):
    cost = Decimal(str(wholesale))
    current = Decimal(str(current))
    if cost <= 0 or current < 0:
        raise ValueError("Invalid wholesale or current price")
    minimum = None
    covered_until = Decimal(0)
    for lower, upper, percent in commission_bands:
        lower = Decimal(str(lower))
        upper = Decimal(str(upper)) if upper is not None else None
        rate = Decimal(str(percent)) / 100
        if lower != covered_until or (upper is not None and upper <= lower):
            raise ValueError("Commission bands must be contiguous and ordered")
        covered_until = upper
        if not 0 <= rate < Decimal("0.75"):
            raise ValueError("Invalid effective commission rate")
        for start, end, smart, cost_factor in (
            (0, 400, 12, "1.25"), (400, 700, 18, "1.25"),
            (700, 3000, 30, "1.25"), (3000, None, 30, "1.15")
        ):
            floor = max(lower, Decimal(start))
            limits = [n for n in (upper, end) if n is not None]
            ceiling = min(limits) if limits else None
            required = (cost * Decimal(cost_factor) + 30 + smart) / (Decimal("0.75") - rate)
            if current >= max(required, floor) and (ceiling is None or current < ceiling):
                candidate = current
            else:
                candidate = max(required, floor, current).to_integral_value(rounding=ROUND_CEILING)
            if ceiling is None or candidate < ceiling:
                minimum = candidate if minimum is None else min(candidate, minimum)
    if covered_until is not None or minimum is None:
        raise ValueError("Missing effective commission coverage")
    return minimum


def old_price(offer_id, final_price):
    """Calculate the configured reference price in UAH for store 47931."""
    price = Decimal(str(final_price))
    if not isinstance(offer_id, str) or not offer_id.strip():
        raise ValueError("Missing OFFERID")
    if not price.is_finite() or price <= 0:
        raise ValueError("Invalid final price")
    digest = sha256(offer_id.encode("utf-8")).digest()
    percent = 30 + int.from_bytes(digest, "big") % 41
    return (price * (100 + percent) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
