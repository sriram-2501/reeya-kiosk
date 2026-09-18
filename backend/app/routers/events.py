import json
import logging

from fastapi import APIRouter

from app.models.schemas import KioskEventRequest, KioskEventResponse
from app.services.db import get_connection, new_uuid

logger = logging.getLogger(__name__)

router = APIRouter(tags=["events"])


@router.post("/events", response_model=KioskEventResponse, status_code=202)
def record_event(event: KioskEventRequest) -> KioskEventResponse:
    """Records a kiosk analytics event. Per ANALYTICS.md, this must never
    fail loudly to the kiosk — a broken analytics insert should never break
    the customer-facing UI, so failures are logged and swallowed rather than
    raised."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO kiosk_events (id, session_id, event_name, occurred_at, payload) VALUES (%s, %s, %s, %s, %s)",
                (
                    new_uuid(),
                    str(event.session_id),
                    event.event_name,
                    event.occurred_at,
                    json.dumps(event.payload),
                ),
            )
    except Exception:
        logger.exception("Failed to record kiosk event: %s", event.event_name)

    return KioskEventResponse(status="accepted")
