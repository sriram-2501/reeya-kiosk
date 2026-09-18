from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # MySQL (replaces Supabase/Postgres+pgvector -- see migrations_mysql/001_schema.sql
    # for the schema and why RLS/pgvector were dropped rather than ported).
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str
    mysql_password: str
    mysql_database: str = "reeya_kiosk"

    # AWS S3
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str
    s3_bucket_name: str

    # Embeddings: hosted via the Gemini API (see app/services/embeddings.py) —
    # switched from a locally-run CLIP model to remove the torch/transformers
    # memory footprint that was crashing Render's backend (OOM at 512MB).
    embedding_model_name: str = "gemini-embedding-2-preview"
    gemini_api_key: str = ""

    # Presigned uploads
    presign_expiry_seconds: int = 300

    # External teammate API
    product_api_url: str = ""
    product_api_key: str = ""

    # Voice search: filter extraction from a transcript (extract_filters.py).
    # Transcription itself happens client-side (browser Web Speech API), not here.
    openai_api_key: str = ""

    # CORS: comma-separated list of allowed frontend origins (e.g.
    # "https://reeya-kioski.vercel.app,http://localhost:5173"). Only matters
    # once frontend and backend are on different origins (i.e. deployed) —
    # the local Vite dev proxy makes requests same-origin, so this isn't
    # needed for local dev.
    allowed_origins: str = "http://localhost:5173"

    # Additionally allow any Vercel preview deployment (they get a new random
    # subdomain per push/PR, e.g. reeya-kioski-<hash>-<team>.vercel.app, so a
    # fixed list in allowed_origins can never keep up with them). Broad by
    # design for dev convenience; narrow this if that's ever a concern.
    allowed_origin_regex: str = r"^https://.*\.vercel\.app$"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
