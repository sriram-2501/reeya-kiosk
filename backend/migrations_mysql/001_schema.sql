-- MySQL schema for the Reeya Kioski backend, replacing the Supabase/Postgres
-- schema (schema.sql / migrations/001-015). Run once against a fresh
-- `reeya_kiosk` database.
--
-- What's deliberately different from the Postgres version, and why:
--   - No pgvector: MySQL has no equivalent extension. `products.embedding`
--     and `uploaded_images.embedding` are JSON arrays of floats instead of
--     a `vector(512)` column, and cosine-similarity ranking now happens in
--     Python (app/routers/image_search.py), not in a SQL query. Fine at this
--     catalog's real scale (hundreds to a few thousand products).
--   - No Row Level Security anywhere: confirmed unused before removing it —
--     the backend always connects with full access (no anon-key / direct-
--     client-to-DB path exists, unlike a Supabase Auth setup), and the
--     original RLS policies only ever constrained a *direct* client-to-
--     Supabase connection that this app never actually has.
--   - UUIDs are CHAR(36) strings, generated in Python (uuid.uuid4()) at
--     insert time instead of a DB-side default — MySQL 8.0's UUID() exists
--     but doesn't format the same way as Postgres's gen_random_uuid(), and
--     generating it in the same app code that already needs the value for
--     the API response is simpler than round-tripping it back out.
--   - `usage` is backtick-quoted everywhere it's used as a column name —
--     it's a MySQL reserved word (GRANT ... ON USAGE).

CREATE TABLE products (
    id                 CHAR(36) PRIMARY KEY,
    source_product_id  BIGINT,
    name               TEXT NOT NULL,
    category           TEXT,
    price              DECIMAL(12,2),
    image_s3_url       TEXT NOT NULL,
    embedding          JSON,               -- array of floats, NULL until scripts/index_catalog.py runs
    embedding_model    VARCHAR(255),       -- e.g. 'gemini-embedding-2-preview' -- used to detect stale embeddings
    created_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    price_range        VARCHAR(20) GENERATED ALWAYS AS (
        CASE
            WHEN price IS NULL OR price = 0 THEN 'Unknown'
            WHEN price < 10000 THEN 'Below 10K'
            WHEN price < 25000 THEN '10K-25K'
            WHEN price < 50000 THEN '25K-50K'
            WHEN price < 100000 THEN '50K-1L'
            ELSE 'Above 1L'
        END
    ) STORED,
    usage              ENUM('daily_wear', 'office_wear', 'party_wear', 'festive', 'bridal'),  -- unpopulated, see project notes
    age_group          ENUM('teens', 'elegant', 'classic'),                                   -- unpopulated, see project notes
    UNIQUE KEY products_source_product_id_key (source_product_id),
    KEY products_price_range_idx (price_range)
);

CREATE TABLE uploaded_images (
    id                    CHAR(36) PRIMARY KEY,
    user_id               CHAR(36) NOT NULL,
    s3_url                TEXT NOT NULL,
    matched_product_ids   JSON NOT NULL,   -- array of product id strings
    embedding             JSON,            -- array of floats, same shape as products.embedding
    created_at            DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY uploaded_images_user_id_idx (user_id)
);

CREATE TABLE search_history (
    id           CHAR(36) PRIMARY KEY,
    user_id      CHAR(36) NOT NULL,
    transcript   TEXT NOT NULL,
    category     ENUM('earrings', 'pendants', 'necklace', 'rings', 'bangles', 'bracelets'),
    price_band   ENUM('below_10k', '10k_25k', '25k_50k', '50k_1l', 'above_1l'),
    age_group    ENUM('teens', 'elegant', 'classic'),
    usage        ENUM('daily_wear', 'office_wear', 'party_wear', 'festive', 'bridal'),
    search_type  VARCHAR(20) NOT NULL DEFAULT 'voice',
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY search_history_user_id_idx (user_id)
);

CREATE TABLE kiosk_events (
    id           CHAR(36) PRIMARY KEY,
    session_id   CHAR(36) NOT NULL,
    event_name   VARCHAR(255) NOT NULL,
    occurred_at  DATETIME NOT NULL,
    payload      JSON NOT NULL,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY kiosk_events_session_id_idx (session_id),
    KEY kiosk_events_event_name_idx (event_name),
    KEY kiosk_events_occurred_at_idx (occurred_at)
);

CREATE TABLE leads (
    id            CHAR(36) PRIMARY KEY,
    phone         VARCHAR(20) NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    session_ids   JSON NOT NULL,          -- array of session_id strings
    item_count    INT,
    total_amount  DECIMAL(12,2),
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY leads_phone_idx (phone)
);
