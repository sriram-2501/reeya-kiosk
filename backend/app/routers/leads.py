import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter

from app.models.schemas import LeadRequest, LeadResponse
from app.services.db import get_connection, new_uuid

router = APIRouter(tags=["leads"])


def normalize_phone(raw: str) -> str:
    """Strips formatting so the same number typed differently (spaces, +91,
    a leading 0) still matches the same stored row. India-specific: a 12-digit
    number starting with the "91" country code, or an 11-digit number with a
    leading trunk "0", is reduced to the bare 10-digit number."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 12 and digits.startswith("91"):
        return digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        return digits[1:]
    return digits


@router.post("/leads", response_model=LeadResponse)
def submit_lead(payload: LeadRequest) -> LeadResponse:
    """Upserts by (normalized) phone number — a repeat submission from the
    same customer, even across separate kiosk sessions, updates one row
    instead of creating a duplicate. `session_ids` accumulates every
    session_id this phone has ever submitted under, so the customer's full
    kiosk_events history across all their visits stays linkable."""
    phone = normalize_phone(payload.phone)
    session_id = str(payload.session_id)

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, session_ids FROM leads WHERE phone = %s", (phone,))
        existing = cur.fetchone()

        if existing:
            lead_id = existing["id"]
            session_ids = json.loads(existing["session_ids"]) if existing["session_ids"] else []
            if session_id not in session_ids:
                session_ids.append(session_id)

            cur.execute(
                "UPDATE leads SET name=%s, session_ids=%s, item_count=%s, total_amount=%s, updated_at=%s WHERE id=%s",
                (
                    payload.name,
                    json.dumps(session_ids),
                    payload.item_count,
                    payload.total_amount,
                    datetime.now(timezone.utc),
                    lead_id,
                ),
            )
        else:
            lead_id = new_uuid()
            cur.execute(
                "INSERT INTO leads (id, phone, name, session_ids, item_count, total_amount) VALUES (%s,%s,%s,%s,%s,%s)",
                (lead_id, phone, payload.name, json.dumps([session_id]), payload.item_count, payload.total_amount),
            )

    return LeadResponse(id=lead_id)
