from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import load_config
from .filer import Filer, acquire_lock
from .notify import Notifier


def main() -> int:
    ap = argparse.ArgumentParser(prog="plex-filer")
    ap.add_argument("-c", "--config", type=Path, default=Path("/etc/plex-filer/config.toml"))
    ap.add_argument("--dry-run", action="store_true",
                    help="print the rename plan; copy nothing, write nothing to the ledger")
    ap.add_argument("--test-email", action="store_true",
                    help="send a test email using the [notify] settings, then exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,  # journald captures stdout under systemd
    )

    cfg = load_config(args.config if args.config.exists() else None)

    if args.test_email:
        if not cfg.notify_enabled:
            logging.error("notify_enabled is false in the config; set it to true first")
            return 1
        ok = Notifier(cfg).send("Plex: test email from plex-filer",
                                "If you can read this, notifications work.")
        return 0 if ok else 1

    lock = acquire_lock(cfg.lock_path)
    if lock is None:
        logging.error("Another instance is running. Exiting.")
        return 1

    filer = Filer(cfg, dry_run=args.dry_run)
    try:
        filer.run()
    except Exception as e:  # last resort: make sure a crash is not silent
        logging.exception("plex-filer crashed")
        filer.notifier.add_error(f"crashed: {type(e).__name__}: {e}")
        return 2
    finally:
        filer.close()
        if not args.dry_run:
            filer.notifier.send()
    return 1 if filer.stats["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
