"""Tools exposed to the LLM, plus the ToolContext that records what happened on the call."""
from __future__ import annotations

from dataclasses import dataclass, field

from .orders import OrderStore, compute_eligibility, normalize_order_id

TOOL_SCHEMAS = [
    {
        "name": "get_order_details",
        "description": (
            "Look up a customer's order by order ID (format ORD-123). Returns status, carrier, tracking ID, "
            "delivery estimate and pre-computed policy eligibility (cancel / return / shipping fee / COD). "
            "Use this whenever the customer asks about an order. Never guess order information."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order ID exactly as the customer said it, e.g. 'ORD-101'."}
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "cancel_order",
        "description": (
            "Cancel an order. Only call this AFTER the customer has explicitly confirmed they want to cancel. "
            "It succeeds only if the order is still Processing; otherwise it returns the reason it cannot be cancelled."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "Order ID, e.g. 'ORD-103'."}},
            "required": ["order_id"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": (
            "Log a ticket for the human support team. Use when you cannot answer from the provided policies, the customer "
            "reports a damaged/defective product, asks for an exception to policy, or asks for a human. "
            "Do not promise a callback time or an outcome."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "One or two sentences describing what the customer needs."},
                "order_id": {"type": "string", "description": "Related order ID, if any."},
            },
            "required": ["reason"],
        },
    },
]


@dataclass
class ToolContext:
    store: OrderStore
    tool_log: list[dict] = field(default_factory=list)
    escalations: list[dict] = field(default_factory=list)

    def last_found_order_id(self) -> str | None:
        for entry in reversed(self.tool_log):
            res = entry["result"]
            if entry["tool"] in ("get_order_details", "cancel_order") and res.get("order_id"):
                return res["order_id"]
        return None


def _lookup(args: dict, ctx: ToolContext) -> tuple[dict | None, dict | None]:
    """Returns (order, error_result)."""
    raw = (args.get("order_id") or "").strip()
    if not raw:
        return None, {
            "found": False,
            "error_code": "MISSING_ORDER_ID",
            "message": "No order ID was provided. Ask the customer for their order ID.",
        }
    order, norm = ctx.store.get(raw)
    if norm is None:
        return None, {
            "found": False,
            "error_code": "INVALID_ORDER_ID",
            "message": f"'{raw}' does not look like a valid order ID (expected e.g. ORD-101). Ask the customer to repeat it.",
        }
    if order is None:
        return None, {
            "found": False,
            "error_code": "ORDER_NOT_FOUND",
            "message": f"No order exists with ID {norm}. Ask the customer to repeat or verify the ID. Do not guess.",
        }
    return order, None


def _get_order_details(args: dict, ctx: ToolContext) -> dict:
    order, err = _lookup(args, ctx)
    if err:
        return err
    visible = {k: v for k, v in order.items() if k != "notes" and v is not None}
    return {"found": True, "order_id": order["order_id"], "order": visible, "eligibility": compute_eligibility(order)}


def _cancel_order(args: dict, ctx: ToolContext) -> dict:
    order, err = _lookup(args, ctx)
    if err:
        return {"success": False, **err}
    elig = compute_eligibility(order)
    if not elig["can_cancel"]:
        return {
            "success": False,
            "order_id": order["order_id"],
            "status": order["status"],
            "reason": elig["cancel_note"],
        }
    ctx.store.cancel(order)
    return {
        "success": True,
        "order_id": order["order_id"],
        "status": "Cancelled",
        "message": "Order cancelled. Do not state refund amounts or timelines; those are not covered by the provided policies.",
    }


def _escalate(args: dict, ctx: ToolContext) -> dict:
    ticket = {
        "ticket_id": f"TKT-{1001 + len(ctx.escalations)}",
        "reason": (args.get("reason") or "").strip(),
        "order_id": normalize_order_id(args.get("order_id")),
    }
    ctx.escalations.append(ticket)
    return {
        "logged": True,
        "ticket_id": ticket["ticket_id"],
        "message": "Ticket logged for the human support team. Do not promise a callback time or an outcome.",
        "order_id": ticket["order_id"],
    }


_HANDLERS = {
    "get_order_details": _get_order_details,
    "cancel_order": _cancel_order,
    "escalate_to_human": _escalate,
}


def execute_tool(name: str, args: dict | None, ctx: ToolContext) -> dict:
    args = args or {}
    handler = _HANDLERS.get(name)
    if handler is None:
        result = {"error": f"Unknown tool '{name}'."}
    else:
        try:
            result = handler(args, ctx)
        except Exception:  # never let a tool bug crash the call
            result = {"error": "Internal error while running the tool. Apologise and offer to escalate."}
    ctx.tool_log.append({"tool": name, "input": args, "result": result})
    return result
