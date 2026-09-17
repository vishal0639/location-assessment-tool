"""Create tables if they don't exist. Safe to call from several processes.

Prototype shortcut: create_all instead of Alembic migrations (see DECISIONS.md).
"""
import logging
import time

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.db import Base, engine

log = logging.getLogger("init_db")


def init_db(retries: int = 30) -> None:
    for attempt in range(retries):
        try:
            with engine.begin() as conn:
                # API and worker both start at once; serialise DDL between them.
                conn.execute(text("SELECT pg_advisory_xact_lock(424242)"))
                Base.metadata.create_all(conn)
            return
        except OperationalError:
            if attempt == retries - 1:
                raise
            log.warning("database not ready, retrying (%s/%s)", attempt + 1, retries)
            time.sleep(1)


if __name__ == "__main__":
    init_db()
    print("database ready")
