"""Mock order database + policy rules.

Policy decisions (can this be cancelled? is it inside the return window?) are computed
here in code and handed to the LLM as facts. The model explains them; it never has to
work them out, which keeps guardrails deterministic.
"""
from __future__ import annotations

import copy
import re

RETURN_WINDOW_DAYS = 7
DAMAGE_REPORT_HOURS = 48
FREE_SHIPPING_ABOVE_INR = 499
SHIPPING_FEE_INR = 50
COD_MAX_INR = 2500

ORDERS_SEED: dict[str, dict] = {
    "ORD-101": {
        "order_id": "ORD-101",
        "customer": "Priya Sharma",
        "product": "Vitamin C Serum (30ml)",
        "value_inr": 699,
        "status": "Out for Delivery",
        "carrier": "BlueDart",
        "tracking_id": "BD-982103",
        "expected_delivery": "by 6 PM today",
        "delivered_days_ago": None,
        "ordered_hours_ago": None,
        "notes": "BlueDart - BD-982103. Expected by 6 PM today",
    },
    "ORD-102": {
        "order_id": "ORD-102",
        "customer": "Rahul Verma",
        "product": "Hydrating Sunscreen SPF 50",
        "value_inr": 499,
        "status": "Delivered",
        "carrier": "Delhivery",
        "tracking_id": "DL-441029",
        "expected_delivery": None,
        "delivered_days_ago": 14,
        "ordered_hours_ago": None,
        "notes": "Delhivery - DL-441029. Delivered 14 days ago",
    },
    "ORD-103": {
        "order_id": "ORD-103",
        "customer": "Ananya Patel",
        "product": "Green Tea Face Wash + Toner",
        "value_inr": 850,
        "status": "Processing",
        "carrier": None,
        "tracking_id": None,
        "expected_delivery": None,
        "delivered_days_ago": None,
        "ordered_hours_ago": 3,
        "notes": "Ordered 3 hours ago. Eligible for cancellation",
    },
}


def public_orders() -> list[dict]:
    """Seed data for the UI's Test Orders panel."""
    return [copy.deepcopy(o) for o in ORDERS_SEED.values()]


# --------------------------------------------------------------------------- ID handling
_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
_FILLER = {"order", "orders", "id", "number", "no", "ord", "is", "my", "the", "for", "of"}


def normalize_order_id(raw: str | None) -> str | None:
    """'ord 101', 'ORD101', 'order one oh one', '101' -> 'ORD-101'. None if no digits found."""
    if not raw:
        return None
    text = re.sub(r"[^a-z0-9\s]", " ", raw.lower())
    digits = ""
    for token in text.split():
        m = re.fullmatch(r"(?:ord|order)(\d+)", token)
        if m:
            digits += m.group(1)
        elif token in _FILLER:
            continue
        elif token.isdigit():
            digits += token
        elif token in _DIGIT_WORDS:
            digits += _DIGIT_WORDS[token]
        else:
            return None  # unrecognised word: do not guess
    return f"ORD-{digits}" if digits else None


# --------------------------------------------------------------------------- policy
def compute_eligibility(order: dict) -> dict:
    status = order["status"]
    value = order["value_inr"]

    # Cancellation
    if status == "Processing":
        cancel = {"can_cancel": True, "cancel_note": "Order is still Processing, so it can be cancelled."}
    elif status in ("Shipped", "Out for Delivery"):
        cancel = {
            "can_cancel": False,
            "cancel_note": f"Order is {status}, so it cannot be cancelled. "
            "The customer may refuse delivery at the doorstep.",
        }
    elif status == "Delivered":
        cancel = {"can_cancel": False, "cancel_note": "Order is already delivered, so it cannot be cancelled."}
    else:
        cancel = {"can_cancel": False, "cancel_note": f"Order status is {status}; no cancellation is possible."}

    # Returns / damage
    days = order.get("delivered_days_ago")
    if status != "Delivered" or days is None:
        ret = {
            "return_status": "NOT_APPLICABLE",
            "return_note": "Returns only apply after delivery; this order has not been delivered.",
            "damage_report_window_open": False,
        }
    else:
        within = days <= RETURN_WINDOW_DAYS
        ret = {
            "return_status": "CONDITIONAL" if within else "NOT_ELIGIBLE",
            "return_note": (
                f"Delivered {days} days ago. Returns are accepted within {RETURN_WINDOW_DAYS} days of delivery, "
                "and only for unopened, unused products in original packaging. "
                + (
                    "It is inside the window, so eligibility depends on the product being unopened, unused and in original packaging; ask the customer."
                    if within
                    else "It is OUTSIDE the window, so it is not eligible for return."
                )
            ),
            "damage_report_window_open": days * 24 <= DAMAGE_REPORT_HOURS,
        }

    # Shipping fee / COD facts (policy says free "above" 499 and fee "below" 499)
    if value > FREE_SHIPPING_ABOVE_INR:
        ship = {"shipping_fee_inr": 0, "shipping_note": "Order value is above Rs 499, so delivery is free."}
    elif value < FREE_SHIPPING_ABOVE_INR:
        ship = {"shipping_fee_inr": SHIPPING_FEE_INR, "shipping_note": "Order value is below Rs 499, so a Rs 50 shipping fee applies."}
    else:
        ship = {
            "shipping_fee_inr": None,
            "shipping_note": "Order value is exactly Rs 499. The policy only defines free delivery ABOVE 499 and a fee BELOW 499, "
            "so do not state a fee; say you will need to confirm and offer to pass it to the team.",
        }

    return {**cancel, **ret, **ship, "cod_eligible": value <= COD_MAX_INR}


# --------------------------------------------------------------------------- store
class OrderStore:
    """Per-call copy of the database, so cancelling ORD-103 in one test call does not
    affect the next call."""

    def __init__(self) -> None:
        self._orders = copy.deepcopy(ORDERS_SEED)

    def get(self, raw_id: str | None) -> tuple[dict | None, str | None]:
        norm = normalize_order_id(raw_id)
        if norm is None:
            return None, None
        return self._orders.get(norm), norm

    def cancel(self, order: dict) -> None:
        order["status"] = "Cancelled"
        order["expected_delivery"] = None
