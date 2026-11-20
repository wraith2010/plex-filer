from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import load_config
from .filer import Filer, acquire_lock


def main() -> int:
    ap = argparse.ArgumentParser(prog="plex-filer")
    ap.add_argument("-c", "--config", type=Path, default=Path("/etc/plex-filer/config.toml"))
    ap.add_argument("--dry-run", action="store_true",
                    help="print the rename plan; copy nothing, write nothing to the ledger")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,  # journald captures stdout under systemd
    )

    cfg = load_config(args.config if args.config.exists() else None)
    lock = acquire_lock(cfg.lock_path)
    if lock is None:
        logging.error("Another instance is running. Exiting.")
        return 1

    filer = Filer(cfg, dry_run=args.dry_run)
    try:
        filer.run()
    finally:
        filer.close()
    return 1 if filer.stats["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
