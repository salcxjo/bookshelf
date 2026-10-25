"""A tiny SQL migration runner.

Schema changes live in migrations/NNN_description.sql, numbered 1, 2, 3 ... with no gaps.
SQLite's built-in `PRAGMA user_version` records how many have been applied, so on startup
only the missing ones run, each inside its own transaction. If one fails, it is rolled
back and the database stays at the previous version.

Rules for migration files:
  * Never edit a migration that has already been merged; add a new one instead.
  * No BEGIN / COMMIT / PRAGMA foreign_keys inside them (the runner handles the first,
    and the pragma does nothing inside a transaction).

Run by hand with:  python -m bookshelf.db.migrate data/library.db
"""
import re
import sqlite3
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
_NAME = re.compile(r"^(\d+)_[\w-]+\.sql$")


def discover(directory: Path = MIGRATIONS_DIR) -> list[tuple[int, Path]]:
    """Return [(number, path), ...] in order, insisting on 1, 2, 3 ... with no gaps."""
    found = []
    for path in directory.glob("*.sql"):
        match = _NAME.match(path.name)
        if not match:
            raise ValueError(f"migration file names must look like 003_add_x.sql, got {path.name!r}")
        found.append((int(match.group(1)), path))
    found.sort()

    numbers = [number for number, _ in found]
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError(f"migrations must be numbered 1, 2, 3 ... with no gaps or repeats, got {numbers}")
    return found


def current_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _backup_before_migrating(conn: sqlite3.Connection, version: int) -> None:
    """Copy an existing database file next to itself before changing it."""
    file = conn.execute("PRAGMA database_list").fetchone()[2]  # '' for in-memory databases
    if not file or not Path(file).exists() or Path(file).stat().st_size == 0:
        return
    target = sqlite3.connect(Path(file).with_name(f"{Path(file).name}.pre-v{version}.bak"))
    try:
        conn.backup(target)
    finally:
        target.close()


def migrate(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> int:
    """Apply any migrations the database hasn't seen. Returns the resulting version."""
    migrations = discover(directory)
    latest = migrations[-1][0] if migrations else 0
    version = current_version(conn)

    if version > latest:
        raise RuntimeError(
            f"database is at schema version {version}, but this code only knows up to {latest}. "
            "Was it created by a newer version of the app?"
        )

    pending = [(number, path) for number, path in migrations if number > version]
    if not pending:
        return version

    _backup_before_migrating(conn, version)

    for number, path in pending:
        script = f"BEGIN;\n{path.read_text(encoding='utf-8')}\nPRAGMA user_version = {number};\nCOMMIT;"
        try:
            conn.executescript(script)
        except Exception:
            if conn.in_transaction:
                conn.rollback()  # DDL is transactional in SQLite, so this undoes the partial migration
            raise
    return pending[-1][0]


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/library.db"
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        before = current_version(connection)
        after = migrate(connection)
    finally:
        connection.close()
    print(f"{db_path}: schema version {before} -> {after}")
