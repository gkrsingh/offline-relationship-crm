"""SQLite access helpers.

One module, three jobs: open a connection with sane pragmas, apply schema.sql,
and load the raw JSON export into the `people` / `applications` tables.
Everything downstream reads from SQLite, not from the JSON files.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB_PATH = REPO_ROOT / "data" / "crm.db"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def load_people(conn: sqlite3.Connection, records: list[dict]) -> int:
    now = utc_now()
    conn.executemany(
        """
        INSERT OR REPLACE INTO people
            (id, full_name, email, linkedin_url, company, title, location, bio,
             source, needs, offers, created_at, ingested_at, merged_into)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        [
            (
                r["id"], r["full_name"], r["email"], r["linkedin_url"], r["company"],
                r["title"], r["location"], r["bio"], r["source"],
                json.dumps(r.get("needs") or []), json.dumps(r.get("offers") or []),
                r["created_at"], now,
            )
            for r in records
        ],
    )
    conn.commit()
    return len(records)


def load_applications(conn: sqlite3.Connection, applications: list[dict]) -> int:
    conn.executemany(
        """
        INSERT OR REPLACE INTO applications
            (person_id, building_now, why_join, contribution, referred_by, submitted_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (a["person_id"], a["building_now"], a["why_join"], a["contribution"],
             a["referred_by"], a["submitted_at"])
            for a in applications
        ],
    )
    conn.commit()
    return len(applications)
