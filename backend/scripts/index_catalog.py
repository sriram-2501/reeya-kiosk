"""One-off / repeatable catalog embedding job.

Usage:
    python -m scripts.index_catalog

Steps:
    1. Fetch all products (S3 image URL + metadata) from the teammate's
       product-image API and upsert them into the `products` table.
    2. Find rows whose embedding is missing or stale (embedding_model doesn't
       match the current EMBEDDING_MODEL_NAME) and (re)compute it.

Safe to re-run: step 2 only ever touches rows that need it, and step 1 upserts
on the teammate API's product id (`source_product_id`) so re-running doesn't
duplicate rows.

Note on scope: the teammate's API deliberately excludes internal cost
breakdown (diamond/gold cost, making charge, tax) — only the final
customer-facing `price` is available, and that's intentional (margin data).
Don't add fields to pull that data without checking with them first.

The feed now includes a WordPress `status` field (e.g. "publish", "draft",
"pending") per product — the feed itself is no longer pre-filtered to
kiosk-visible products, so we only upsert `status == "publish"` rows here.
Anything else (draft/pending/etc.) is skipped, same as a missing image.
"""

import logging
import sys
import uuid

import requests

from app.config import get_settings
from app.services.db import get_connection
from app.services.embeddings import EMBEDDING_MODEL_NAME, get_image_embedding
from app.services.s3 import download_image_bytes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("index_catalog")

PER_PAGE = 100


def fetch_products_from_teammate_api() -> list[dict]:
    """Fetches the full product catalog (paginated) from the teammate's
    WordPress-backed product API.

    Auth: X-API-Key header. Pagination: /products?page=N&per_page=100,
    response includes `total_pages` — loop until exhausted.
    """
    settings = get_settings()
    headers = {"X-API-Key": settings.product_api_key}
    products: list[dict] = []
    page = 1

    while True:
        response = requests.get(
            f"{settings.product_api_url}/products",
            headers=headers,
            params={"page": page, "per_page": PER_PAGE},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()

        products.extend(payload["products"])
        total_pages = payload["total_pages"]
        if page >= total_pages:
            break
        page += 1

    return products


def upsert_products(products: list[dict]) -> None:
    """Upserts product metadata only — never touches embedding columns, so a
    re-run doesn't wipe out embeddings computed in a previous run.

    Simplification: the API returns `categories` (array) and `images` (array)
    per product; we store just the first category and first (featured) image,
    matching this project's single-category/single-image schema. Products
    with no images can't be embedded, so they're skipped with a log line.

    Only `status == "publish"` products are indexed — draft/pending/etc. are
    not meant to be visible in the kiosk.

    Products with no real price (missing or 0 — the teammate feed uses 0 as
    a placeholder for "not priced yet", not an actual free item) are skipped
    too: they'd otherwise show up in the kiosk as "₹0", which reads as a data
    bug rather than a real product.

    Products with "testing" in their name are also skipped — internal test
    entries in the teammate's WordPress catalog (e.g. someone's scratch entry
    with a screenshot as its "image") that were never meant to reach the
    kiosk.
    """
    records = []
    for product in products:
        if product.get("status") != "publish":
            logger.info(
                "Skipping product id=%s (%s): status=%s",
                product["id"],
                product.get("name"),
                product.get("status"),
            )
            continue

        name = product.get("name") or ""
        if "testing" in name.lower():
            logger.info("Skipping product id=%s (%s): test entry", product["id"], name)
            continue

        price = product.get("price")
        try:
            has_real_price = price is not None and float(price) > 0
        except (TypeError, ValueError):
            has_real_price = False
        if not has_real_price:
            logger.info(
                "Skipping product id=%s (%s): no real price set (price=%r)",
                product["id"],
                product.get("name"),
                price,
            )
            continue

        images = product.get("images") or []
        # Some catalog entries return a non-URL placeholder (e.g. `false`) as
        # an image entry instead of omitting it — only accept real strings.
        image_url = next((img for img in images if isinstance(img, str) and img), None)
        if image_url is None:
            logger.warning("Skipping product id=%s (%s): no valid image URL", product["id"], product.get("name"))
            continue

        categories = product.get("categories") or []
        records.append(
            (
                product["id"],
                product["name"],
                categories[0]["name"] if categories else None,
                product.get("price"),
                image_url,
            )
        )

    if not records:
        return

    with get_connection() as conn, conn.cursor() as cur:
        for source_product_id, name, category, price, image_s3_url in records:
            # ON DUPLICATE KEY UPDATE matches Postgres's upsert(on_conflict=
            # "source_product_id") -- a fresh id is only generated on genuine
            # insert (MySQL's ON DUPLICATE KEY UPDATE keeps the existing row's
            # id untouched, so `id` isn't part of the UPDATE clause at all).
            cur.execute(
                """
                INSERT INTO products (id, source_product_id, name, category, price, image_s3_url)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    name = VALUES(name),
                    category = VALUES(category),
                    price = VALUES(price),
                    image_s3_url = VALUES(image_s3_url)
                """,
                (str(uuid.uuid4()), source_product_id, name, category, price, image_s3_url),
            )
    logger.info("Upserted %d product rows", len(records))


def fetch_products_needing_embedding() -> list[dict]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, image_s3_url FROM products WHERE embedding IS NULL OR embedding_model IS NULL OR embedding_model != %s",
            (EMBEDDING_MODEL_NAME,),
        )
        return cur.fetchall()


def embed_and_update(product: dict) -> bool:
    import json

    try:
        image_bytes = download_image_bytes(product["image_s3_url"])
        embedding = get_image_embedding(image_bytes)
    except Exception:
        logger.exception("Failed to embed product id=%s (%s)", product["id"], product["image_s3_url"])
        return False

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE products SET embedding = %s, embedding_model = %s WHERE id = %s",
            (json.dumps(embedding), EMBEDDING_MODEL_NAME, product["id"]),
        )
    return True


def run() -> None:
    products = fetch_products_from_teammate_api()
    logger.info("Fetched %d products from teammate API", len(products))
    upsert_products(products)

    pending = fetch_products_needing_embedding()
    logger.info("%d products need (re)embedding", len(pending))

    succeeded = 0
    failed = 0
    for i, product in enumerate(pending, start=1):
        if embed_and_update(product):
            succeeded += 1
        else:
            failed += 1
        if i % 25 == 0 or i == len(pending):
            logger.info("Progress: %d/%d (succeeded=%d, failed=%d)", i, len(pending), succeeded, failed)

    logger.info("Done. succeeded=%d failed=%d", succeeded, failed)


if __name__ == "__main__":
    try:
        run()
    except Exception:
        logger.exception("Indexing job failed")
        sys.exit(1)
