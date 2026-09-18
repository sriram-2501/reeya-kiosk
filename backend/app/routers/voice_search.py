import logging
from uuid import UUID

from fastapi import APIRouter

from app.models.schemas import ExtractedFilters, SearchHistoryItem, VoiceMatch, VoiceSearchRequest, VoiceSearchResponse
from app.services.db import get_connection, new_uuid
from app.services.extract_filters import extract_search_filters
from app.services.filter_constants import CATEGORY_PATTERNS, PRICE_BAND_RANGES

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voice-search"])

MATCH_LIMIT = 20


def _merge_filter(extracted: str | None, explicit: list[str] | None) -> list[str] | None:
    """Explicit multi-select choices (Refine Search's preference pills) fully
    replace whatever the LLM extracted from the transcript — picking any
    explicit values means the user overrode that filter, one value or many."""
    if explicit:
        return [value.strip().lower() for value in explicit if value.strip()] or None
    return [extracted] if extracted else None


@router.post("/voice-search", response_model=VoiceSearchResponse)
def voice_search(payload: VoiceSearchRequest) -> VoiceSearchResponse:
    transcript = payload.transcript
    user_id = payload.user_id

    try:
        extracted = extract_search_filters(transcript)
    except Exception:
        # LLM extraction failing (API error, quota, etc.) shouldn't surface
        # as a scary error on the kiosk — treat it the same as "didn't
        # recognize anything said", which falls through to "no results"
        # below rather than an unfiltered product list.
        logger.exception(
            "Filter extraction failed for transcript=%r; treating as unrecognized", transcript
        )
        extracted = {"category": None, "price_band": None, "age_group": None, "usage": None}

    category = payload.category.strip().lower() if payload.category else extracted.get("category")
    price_bands = _merge_filter(extracted.get("price_band"), payload.price_band)
    age_groups = _merge_filter(extracted.get("age_group"), payload.age_group)
    usages = _merge_filter(extracted.get("usage"), payload.usage)

    understood = bool(category) or bool(price_bands) or bool(age_groups) or bool(usages)
    matches: list[dict] = []

    with get_connection() as conn, conn.cursor() as cur:
        # A category tap with no voice/text query at all (plain category browse)
        # has an empty transcript and no filters yet — that's not "didn't
        # understand", it's just browsing, so it still runs the (unfiltered)
        # query below. A real transcript that recognized nothing, though, should
        # report no results instead of silently showing an unrelated product
        # list that looks like a match but isn't.
        if not transcript.strip() or understood:
            where_clauses = []
            params: list = []

            if category and category in CATEGORY_PATTERNS:
                where_clauses.append("category REGEXP %s")
                params.append(CATEGORY_PATTERNS[category])

            valid_bands = [band for band in price_bands or [] if band in PRICE_BAND_RANGES]
            if valid_bands:
                # price = 0 means "no real price set", not a genuinely
                # free/near-free item — exclude it explicitly, since
                # price >= 0 would otherwise let it leak into the cheapest band.
                where_clauses.append("price > 0")
                or_parts = []
                for band in valid_bands:
                    min_price, max_price = PRICE_BAND_RANGES[band]
                    if max_price is not None:
                        or_parts.append("(price >= %s AND price < %s)")
                        params.extend([min_price, max_price])
                    else:
                        or_parts.append("price >= %s")
                        params.append(min_price)
                where_clauses.append("(" + " OR ".join(or_parts) + ")")

            if age_groups:
                placeholders = ",".join(["%s"] * len(age_groups))
                where_clauses.append(f"age_group IN ({placeholders})")
                params.extend(age_groups)

            if usages:
                placeholders = ",".join(["%s"] * len(usages))
                where_clauses.append(f"`usage` IN ({placeholders})")
                params.extend(usages)

            sql = "SELECT id, name, image_s3_url, price, category FROM products"
            if where_clauses:
                sql += " WHERE " + " AND ".join(where_clauses)
            sql += " LIMIT %s"
            params.append(MATCH_LIMIT)

            cur.execute(sql, params)
            matches = cur.fetchall()

        history_id = new_uuid()
        cur.execute(
            "INSERT INTO search_history (id, user_id, transcript, category, price_band, age_group, `usage`, search_type) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                history_id,
                str(user_id),
                transcript,
                category,
                # search_history's columns hold a single value each (see the
                # ENUM columns in migrations_mysql/001_schema.sql) — when
                # multiple were picked, only the first is logged here.
                # Product filtering above is unaffected; this only makes the
                # analytics log lossy for multi-select searches.
                price_bands[0] if price_bands else None,
                age_groups[0] if age_groups else None,
                usages[0] if usages else None,
                "voice",
            ),
        )

    return VoiceSearchResponse(
        search_history_id=history_id,
        transcript=transcript,
        extracted_filters=ExtractedFilters(
            category=category,
            price_band=price_bands,
            age_group=age_groups,
            usage=usages,
        ),
        matches=[VoiceMatch(**p) for p in matches],
    )


@router.get("/search-history", response_model=list[SearchHistoryItem])
def get_search_history(user_id: UUID) -> list[SearchHistoryItem]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, user_id, transcript, category, price_band, age_group, `usage`, search_type, created_at "
            "FROM search_history WHERE user_id = %s ORDER BY created_at DESC",
            (str(user_id),),
        )
        rows = cur.fetchall()
    return [SearchHistoryItem(**row) for row in rows]
