import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

MIGRATIONS_DIR = Path("./migrations")
MIGRATION_PATTERN = re.compile(r"^(\d+)_.*\.sql$")
DB_URI = os.getenv("DB_URI", "tickets.sqlite")

# How long (ms) to wait on a locked DB before giving up. Relevant if two
# job invocations ever overlap (e.g. a slow run bleeding into the next
# 5-minute cron trigger).
BUSY_TIMEOUT_MS = 30_000

@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")

class MigrationError(RuntimeError):
    """Raised when migration discovery or application fails."""


def ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Find and validate all migration files in `directory`, sorted by version."""
    if not directory.exists():
        raise MigrationError(f"Migrations directory not found: {directory}")

    migrations: list[Migration] = []

    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_PATTERN.match(path.name)
        if not match:
            raise MigrationError(f"Migration file does not match pattern NNNN_name.sql: {path.name}")

        version = int(match.group(1))
        migrations.append(Migration(version=version, name=path.stem, path=path))

    versions = [m.version for m in migrations]

    if len(versions) != len(set(versions)):
        duplicates = {v for v in versions if versions.count(v) > 1}
        raise MigrationError(f"Duplicate migration version numbers: {sorted(duplicates)}")

    migrations.sort(key=lambda m: m.version)

    return migrations


def status(db_path: str, migrations_dir: Path = MIGRATIONS_DIR) -> None:
    """Print applied vs. pending migrations without applying anything."""
    conn = sqlite3.connect(db_path)

    try:
        ensure_migrations_table(conn)
        done = applied_versions(conn)
        all_migrations = discover_migrations(migrations_dir)

        for m in all_migrations:
            marker = "applied" if m.version in done else "pending"
            print(f"[{marker:7}] {m.version:04d}  {m.name}")

        if not all_migrations:
            print("No migration files found.")
    finally:
        conn.close()


def pending_migrations(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[Migration]:
    ensure_migrations_table(conn)
    done = applied_versions(conn)

    return [m for m in discover_migrations(migrations_dir) if m.version not in done]


def apply_migration(conn: sqlite3.Connection, migration: Migration) -> None:
    """Apply a single migration file as one script, then record it.

    Note: sqlite3's executescript() issues an implicit COMMIT before running,
    so a multi-statement migration file is NOT atomic as a whole on its own.
    Each migration file should therefore represent one coherent, ideally
    single-purpose change. For strict per-file atomicity, split the file's
    statements and run them individually inside an explicit transaction
    instead of executescript().
    """

    try:
        conn.executescript(migration.sql)
        conn.execute("INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
            (migration.version, migration.name),)
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        raise MigrationError(f"Migration {migration.name} failed: {exc}") from exc


def migrate(db_path: str, migrations_dir: Path = MIGRATIONS_DIR, verbose: bool = True) -> int:
    """Apply all pending migrations. Returns the number applied.

    Idempotent — safe to call on every process start. Intended to run at the
    top of the app entrypoint before any other DB access.
    """

    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_MS / 1_000)

    try:
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys = ON")

        pending = pending_migrations(conn, migrations_dir)

        if not pending:
            if verbose:
                print("Database is up to date")
            return 0

        for migration in pending:
            if verbose:
                print(f"Applying {migration.name}...")
            apply_migration(conn, migration)

        return len(pending)
    finally:
        conn.close()


def main():
    """Entrypoint for `uv run migrate [db_path] [--status]`."""
    args = sys.argv[1:]
    show_status = "--status" in args
    positional = [a for a in args if a != "--status"]
    db_path = positional[0] if positional else DB_URI

    if show_status:
        status(db_path)
    else:
        migrate(db_path)


if __name__ == "__main__":
    main()