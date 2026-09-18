import json
import logging
import re

import requests
from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import ImageSearchRequest, ImageSearchResponse, ProductMatch
from app.services.db import get_connection, new_uuid
from app.services.embeddings import get_image_embedding
from app.services.filter_constants import CATEGORY_PATTERNS
from app.services.storage import download_image_bytes

logger = logging.getLogger(__name__)

router = APIRouter(tags=["image-search"])

MATCH_COUNT = 20


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Replaces pgvector's `<=>` operator (1 - cosine distance), which has no
    MySQL equivalent — computed here in Python instead of in the database.
    Fine at this catalog's real scale (see migrations_mysql/001_schema.sql);
    revisit if the catalog ever grows large enough for this per-request full
    scan to matter."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


@router.post("/image-search", response_model=ImageSearchResponse)
def image_search(
    payload: ImageSearchRequest,
    category: str | None = Query(default=None, description="Optional category pre-filter"),
) -> ImageSearchResponse:
    try:
        image_bytes = download_image_bytes(payload.s3_url)
    except requests.RequestException as exc:
        raise HTTPException(status_code=400, detail=f"Could not download image from s3_url: {exc}") from exc

    try:
        embedding = get_image_embedding(image_bytes)
    except Exception as exc:
        # An unhandled exception here would otherwise escape past FastAPI's
        # CORSMiddleware (only HTTPException responses get CORS headers
        # attached), so the browser sees a bare, origin-less 500 and reports
        # it as a generic "Failed to fetch" instead of a real error.
        logger.exception("Embedding generation failed for s3_url=%s", payload.s3_url)
        raise HTTPException(status_code=502, detail=f"Embedding generation failed: {exc}") from exc

    # A known category label (e.g. "Earrings") -> its curated regex pattern
    # (e.g. "earring|stud"); an unrecognized string falls back to a literal,
    # regex-escaped substring match on itself, so arbitrary category text
    # still works, just without the curated synonyms.
    category_pattern = None
    if category:
        category_pattern = CATEGORY_PATTERNS.get(category.strip().lower(), re.escape(category))

    with get_connection() as conn, conn.cursor() as cur:
        if category_pattern:
            cur.execute(
                "SELECT id, name, image_s3_url, price, embedding FROM products WHERE embedding IS NOT NULL AND category REGEXP %s",
                (category_pattern,),
            )
        else:
            cur.execute(
                "SELECT id, name, image_s3_url, price, embedding FROM products WHERE embedding IS NOT NULL"
            )
        candidates = cur.fetchall()

        scored = sorted(
            (
                (_cosine_similarity(embedding, json.loads(row["embedding"])), row)
                for row in candidates
            ),
            key=lambda item: item[0],
            reverse=True,
        )[:MATCH_COUNT]

        matches = [
            ProductMatch(
                id=row["id"],
                name=row["name"],
                image_s3_url=row["image_s3_url"],
                price=row["price"],
                similarity=similarity,
            )
            for similarity, row in scored
        ]
        matched_product_ids = [str(match.id) for match in matches]

        uploaded_image_id = new_uuid()
        cur.execute(
            "INSERT INTO uploaded_images (id, user_id, s3_url, matched_product_ids, embedding) VALUES (%s,%s,%s,%s,%s)",
            (
                uploaded_image_id,
                str(payload.user_id),
                payload.s3_url,
                json.dumps(matched_product_ids),
                json.dumps(embedding),
            ),
        )

    return ImageSearchResponse(uploaded_image_id=uploaded_image_id, matches=matches)
