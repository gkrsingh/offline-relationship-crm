"""Create data/crm.db and load the raw export into it.

Run:
    python backend/scripts/init_db.py
    python backend/scripts/init_db.py --reset     # drop the file and rebuild

Idempotent: re-running reloads the same records over the top. `--reset` is only
needed when the schema itself has changed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.app import db  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH)
    parser.add_argument("--raw", type=Path, default=REPO_ROOT / "data" / "raw" / "people_raw.json")
    parser.add_argument("--applications", type=Path,
                        default=REPO_ROOT / "data" / "raw" / "applications.json")
    parser.add_argument("--reset", action="store_true", help="delete the database file first")
    args = parser.parse_args()

    if args.reset and args.db.exists():
        args.db.unlink()
        print(f"removed {args.db}")

    if not args.raw.exists():
        raise SystemExit(f"{args.raw} not found -- run backend/scripts/generate_data.py first")

    records = json.loads(args.raw.read_text(encoding="utf-8"))
    applications = json.loads(args.applications.read_text(encoding="utf-8")) \
        if args.applications.exists() else []

    conn = db.connect(args.db)
    db.apply_schema(conn)
    n_people = db.load_people(conn, records)
    n_apps = db.load_applications(conn, applications)

    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    conn.close()

    print(f"database   : {args.db}")
    print(f"tables     : {len(tables)} ({', '.join(tables)})")
    print(f"people     : {n_people}")
    print(f"applications: {n_apps}")


if __name__ == "__main__":
    main()
