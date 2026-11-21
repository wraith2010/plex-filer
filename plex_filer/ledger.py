"""SQLite record of source files already handled.

The TV source dir is never drained (it doubles as the seeding dir), so without
this every run would re-offer the whole thing. The movie source is drained
(delete_movie_source); there the ledger also lets a failed delete be retried. A file is keyed on
size + mtime + relative path: re-downloading or repacking changes the key and
re-queues it; `touch`ing a file in the source re-queues it by hand.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class Ledger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS handled (
                 key TEXT PRIMARY KEY,
                 status TEXT NOT NULL,   -- filed | duplicate | junk | review
                 dest TEXT,
                 ts REAL NOT NULL)"""
        )
        self.db.commit()

    @staticmethod
    def key(kind: str, rel: str, size: int, mtime: float) -> str:
        return f"{kind}:{size}:{int(mtime)}:{rel}"

    def seen(self, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM handled WHERE key=?", (key,)).fetchone() is not None

    def get(self, key: str) -> tuple[str, str | None] | None:
        """(status, dest) for a handled file, or None."""
        return self.db.execute("SELECT status, dest FROM handled WHERE key=?", (key,)).fetchone()

    def record(self, key: str, status: str, dest: str | None = None) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO handled(key,status,dest,ts) VALUES(?,?,?,?)",
            (key, status, dest, time.time()),
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()
