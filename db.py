import os
import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

def connect_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        autocommit=False,
        connect_timeout=15,
    )
