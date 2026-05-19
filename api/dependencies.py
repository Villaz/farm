"""FastAPI dependency providers (database connection)."""

import logging
import os
import sqlite3
from pathlib import Path
from typing import Annotated, Generator

from fastapi import Depends

logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("DATABASE_URL", "../data/cows.db"))


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Open a SQLite connection with foreign keys enabled and close it after the request."""
    logger.info("Using %s", DB_PATH)
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
    finally:
        con.close()


DbDep = Annotated[sqlite3.Connection, Depends(get_db)]
