"""Post-call structured summary. Uses a forced tool call so the output always matches the schema."""
from __future__ import annotations

import json
import logging

from .config import Settings
from .groq_client import complete, make_client
from .orders import normalize_order_id
from .tools import ToolContext

log = logging.getLogger("aura.summary")

INTENTS = [
    "ORDER_TRACKING", "CANCELLATION", "RETURN_REFUND", "SHIPPING_QUERY", "PAYMENT_COD_QUERY",
    "PRODUCT_QUERY", "COMPLAINT", "OUT_OF_SCOPE", "OTHER",
]
STATUSES = ["RESOLVED", "UNRESOLVED", "ESCALATED", "ABANDONED"]

OUTCOME_TOOL = {
    "name": "record_call_outcome",
    "description": "Record the structured outcome of this customer support call.",
    "input_schema": {
        "type": "object",
        "properties": {
            "customer_intent": {"type": "string", "enum": INTENTS, "description": "The customer's main reason for calling."},
            "order_id": {"type": "string", "description": "Main order ID discussed, e.g. ORD-101. Empty string if none."},
            "resolution_status": {"type": "string", "enum": STATUSES},
            "call_summary": {"type": "string", "description": "2-3 factual sentences: what was asked, what Aria did or said, the outcome."},
            "actions_taken": {"type": "array", "items": {"type": "string"}, "description": "Concrete actions, e.g. 'Cancelled ORD-103'. Empty if none."},
            "follow_up_required": {"type": "boolean", "description": "True if a human or the customer still needs to act."},
        },
        "required": ["customer_intent", "order_id", "resolution_status", "call_summary", "actions_taken", "follow_up_required"],
    },
}

SUMMARY_SYSTEM = """You write structured outcomes for customer support calls at Aura Skincare.
Use the transcript, and treat the tool activity log as ground truth for what actually happened (for example an order is only cancelled if cancel_order reported success).
resolution_status:
- RESOLVED: the customer got a definitive answer or outcome. This includes a polite, policy-based "no" (say so in call_summary).
- ESCALATED: a ticket was logged for the human team (escalate_to_human was used).
- UNRESOLVED: Aria could not help, information was missing, or the call ended mid-request.
- ABANDONED: the customer never made a real request.
Be factual. Never invent details that are not in the transcript or tool log."""


def _format_transcript(transcript: list[dict]) -> str:
    return "\n".join(f"{'Customer' if t['role'] == 'user' else 'Aria'}: {t['text']}" for t in transcript)


def fallback_outcome(transcript: list[dict], ctx: ToolContext) -> dict:
    """Heuristic summary used when the LLM call fails; built only from facts in the tool log."""
    tools_used = [e["tool"] for e in ctx.tool_log]
    user_turns = [t for t in transcript if t["role"] == "user"]
    if not user_turns:
        intent, status, text = "OTHER", "ABANDONED", "The customer did not make a request during the call."
    else:
        if "cancel_order" in tools_used:
            intent = "CANCELLATION"
        elif "get_order_details" in tools_used:
            intent = "ORDER_TRACKING"
        else:
            intent = "OTHER"
        status = "ESCALATED" if ctx.escalations else "UNRESOLVED"
        text = "Automatic summary (the summariser was unavailable). Review the transcript for details."
    actions = [
        f"Cancelled {e['result']['order_id']}" for e in ctx.tool_log
        if e["tool"] == "cancel_order" and e["result"].get("success")
    ] + [f"Logged ticket {t['ticket_id']}" for t in ctx.escalations]
    return {
        "customer_intent": intent,
        "order_id": ctx.last_found_order_id(),
        "resolution_status": status,
        "call_summary": text,
        "actions_taken": actions,
        "follow_up_required": bool(ctx.escalations),
    }


class Summarizer:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.client = make_client(settings.groq_api_key)

    async def summarize(self, transcript: list[dict], ctx: ToolContext) -> dict:
        if not any(t["role"] == "user" for t in transcript):
            return fallback_outcome(transcript, ctx)
        prompt = (
            f"Transcript:\n{_format_transcript(transcript)}\n\n"
            f"Tool activity log (ground truth):\n{json.dumps(ctx.tool_log, ensure_ascii=False)}"
        )
        try:
            choice = await complete(self.client, {
                "model": self.s.summary_model,
                "max_tokens": 600,
                "temperature": 0,
                "messages": [{"role": "system", "content": SUMMARY_SYSTEM}, {"role": "user", "content": prompt}],
                "tools": [{"type": "function", "function": {
                    "name": OUTCOME_TOOL["name"], "description": OUTCOME_TOOL["description"],
                    "parameters": OUTCOME_TOOL["input_schema"]}}],
                "tool_choice": {"type": "function", "function": {"name": OUTCOME_TOOL["name"]}},
            })
            data = json.loads(choice["message"]["tool_calls"][0]["function"]["arguments"])
        except Exception:
            log.exception("summary generation failed; using fallback")
            return fallback_outcome(transcript, ctx)

        order_id = normalize_order_id(data.get("order_id")) or ctx.last_found_order_id()
        return {
            "customer_intent": data.get("customer_intent", "OTHER"),
            "order_id": order_id,
            "resolution_status": data.get("resolution_status", "UNRESOLVED"),
            "call_summary": data.get("call_summary", ""),
            "actions_taken": data.get("actions_taken", []),
            "follow_up_required": bool(data.get("follow_up_required", False)),
        }
