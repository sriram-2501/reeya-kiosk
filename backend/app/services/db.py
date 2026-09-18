"""MySQL connection helper, replacing app/services/supabase.py.

Callers open a connection per request via `get_connection()` (a context
manager) rather than sharing a single long-lived client the way
`get_supabase_client()` did — PyMySQL connections aren't safe to share across
concurrent requests the way supabase-py's HTTP-based client was. At this
app's real traffic (one kiosk device, not a public API under load), a fresh
connection per request is simple and fast enough; revisit with a real
connection pool (e.g. DBUtils' PooledDB) only if that assumption stops holding.

`new_uuid()` replaces Postgres's `gen_random_uuid()` default -- IDs are
generated here in Python instead of by the database, since every caller
already needs the value back for its own response anyway.
"""

import uuid
from contextlib import contextmanager

import pymysql
import pymysql.cursors

from app.config import get_settings


@contextmanager
def get_connection():
    settings = get_settings()
    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )
    try:
        yield conn
    finally:
        conn.close()


def new_uuid() -> str:
    return str(uuid.uuid4())
