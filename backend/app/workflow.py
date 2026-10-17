"""The work-item workflow (state machine), enforced on the server.

    open ──► in_progress ──► awaiting_approval ──► resolved ──► closed
                  ▲                 │   (lead only)    │
                  └─── reject ──────┘                  │
                  ▲        (lead only)                 │
                  └────────────── reopen ──────────────┘

Any transition that is not listed here is rejected with 409 `invalid_transition`
(for example open -> closed).
"""
from .errors import ApiError, forbidden

STATUSES = ["open", "in_progress", "awaiting_approval", "resolved", "closed"]

TRANSITIONS: dict[str, set[str]] = {
    "open": {"in_progress"},
    "in_progress": {"awaiting_approval"},
    "awaiting_approval": {"resolved", "in_progress"},
    "resolved": {"closed", "in_progress"},
    "closed": set(),
}

# Transitions that only a lead of the item's team may perform.
LEAD_ONLY = {
    ("awaiting_approval", "resolved"),     # approve
    ("awaiting_approval", "in_progress"),  # reject / send back
}

# Statuses that count as "done" (not shown in the needs-attention views).
DONE_STATUSES = ("resolved", "closed")


def check_transition(item: dict, to_status: str, actor_is_lead: bool) -> None:
    from_status = item["status"]
    if to_status not in STATUSES:
        raise ApiError(422, "invalid_status", f"Unknown status '{to_status}'")
    if to_status not in TRANSITIONS[from_status]:
        allowed = sorted(TRANSITIONS[from_status]) or ["(none, item is closed)"]
        raise ApiError(
            409, "invalid_transition",
            f"Cannot move from '{from_status}' to '{to_status}'. Allowed: {', '.join(allowed)}",
        )
    if (from_status, to_status) in LEAD_ONLY and not actor_is_lead:
        raise forbidden("Only a team lead can approve or reject an item")
    if to_status == "in_progress" and item["assignee_id"] is None:
        raise ApiError(409, "needs_assignee", "Claim or assign the item before starting work on it")


def allowed_next(status: str) -> list[str]:
    return sorted(TRANSITIONS[status])
