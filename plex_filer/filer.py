from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .identify import Identifier, Identity
from .ledger import Ledger
from .naming import LibraryIndex, movie_dest, tv_dest
from .notify import Notifier

log = logging.getLogger("plex-filer")


@dataclass
class Candidate:
    path: Path
    rel: str
    size: int
    key: str


class Filer:
    def __init__(self, cfg: Config, dry_run: bool = False):
        self.cfg = cfg
        self.dry_run = dry_run
        self.ledger = Ledger(cfg.db_path)
        self.ident = Identifier(cfg.model, cfg.api_key_env)  # dry-run still calls Claude
        self.stats = {"filed": 0, "duplicate": 0, "junk": 0, "review": 0, "error": 0,
                      "source_deleted": 0}
        self.notifier = Notifier(cfg)
        self._drain = False  # set per mode in _process: delete sources once handled

    def _error(self, msg: str, count: int = 1) -> None:
        log.error(msg)
        self.stats["error"] += count
        self.notifier.add_error(msg)

    # ------------------------------------------------------------------ scan
    def scan(self, mode: str, source: Path) -> list[Candidate]:
        if not source.is_dir():
            self._error(f"[{mode}] source missing or unmounted: {source}")
            return []
        cutoff = time.time() - self.cfg.age_minutes * 60
        min_bytes = self.cfg.min_video_mb * 1024 * 1024
        found: list[Candidate] = []
        for p in source.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in self.cfg.video_extensions:
                continue
            st = p.stat()
            if st.st_mtime > cutoff:
                continue  # still downloading, or too fresh
            rel = p.relative_to(source).as_posix()
            key = Ledger.key(mode, rel, st.st_size, st.st_mtime)
            if self.ledger.seen(key):
                if self._drain:  # handled on an earlier run but still here: retry the delete
                    self._drain_handled(p, rel, key)
                continue
            if any(m in p.name.lower() for m in self.cfg.sample_markers) or st.st_size < min_bytes:
                log.info("Not an episode/movie, skipping: %s (%d MB)", rel, st.st_size >> 20)
                self._record(key, "junk")
                self._delete_source(p, rel)
                continue
            found.append(Candidate(p, rel, st.st_size, key))
        return sorted(found, key=lambda c: c.rel)

    # ------------------------------------------------------------------- run
    def run(self) -> None:
        self._process("tv", self.cfg.tv_source, self.cfg.tv_library)
        self._process("movie", self.cfg.movie_source, self.cfg.movie_library)
        log.info("Summary: %s", self.stats)
        self._report_review_backlog()

    def _process(self, mode: str, source: Path, library: Path) -> None:
        self._drain = mode == "movie" and self.cfg.delete_movie_source
        try:
            self._process_files(mode, source, library)
        finally:
            if self._drain and not self.dry_run:
                self._prune_source(source)
            self._drain = False

    def _process_files(self, mode: str, source: Path, library: Path) -> None:
        cands = self.scan(mode, source)
        if not cands:
            log.info("[%s] nothing new", mode)
            return
        if not library.is_dir():
            self._error(f"[{mode}] library missing or unmounted, {len(cands)} file(s) not processed: {library}")
            return
        log.info("[%s] %d new file(s)", mode, len(cands))

        index = LibraryIndex.from_dir(library) if mode == "tv" else LibraryIndex([])
        known = index.names
        n = self.cfg.batch_size
        for i in range(0, len(cands), n):
            batch = cands[i : i + n]
            try:
                results = self.ident.identify(mode, [c.rel for c in batch], known)
            except Exception as e:  # API down, rate limited, etc: retry next run
                self._error(f"[{mode}] Claude call failed, {len(batch)} file(s) will retry next run: {e}")
                continue
            for idx, cand in enumerate(batch):
                ident = results.get(idx)
                self._handle(mode, cand, ident, library, index)

    # ---------------------------------------------------------------- handle
    def _handle(self, mode: str, c: Candidate, ident: Identity | None,
                library: Path, index: LibraryIndex) -> None:
        if ident is None:
            return self._review(c, ident, "no answer from Claude")
        if ident.kind == "junk":
            log.info("Junk per Claude (%s): %s", ident.notes, c.rel)
            self._record(c.key, "junk")
            return self._delete_source(c.path, c.rel)
        if ident.confidence < self.cfg.min_confidence:
            return self._review(c, ident, f"low confidence {ident.confidence:.2f}")

        want = "episode" if mode == "tv" else "movie"
        if ident.kind != want or not ident.title:
            return self._review(c, ident, f"identified as {ident.kind!r}, expected {want!r}")

        ext = c.path.suffix
        if mode == "tv":
            if ident.season is None or ident.episode is None:
                return self._review(c, ident, "missing season/episode")
            folder = index.resolve(ident.title, ident.year)
            dest = tv_dest(library, folder, ident.season, ident.episode, ident.episode_end,
                           ext, self.cfg.pad_season_folder)
            if folder not in index.names:
                index.names.append(folder)  # later items in this run join the same folder
        else:
            dest = movie_dest(library, ident.title, ident.year, ext)

        # Keep the destination inside the library no matter what Claude returned.
        if library.resolve() not in dest.resolve().parents:
            return self._review(c, ident, "destination escaped library")

        if dest.exists():
            log.info("Already in library (%s): %s", dest.name, c.rel)
            self._record(c.key, "duplicate", str(dest))
            return self._delete_source(c.path, c.rel)

        log.info("%s%s -> %s  (%.2f, %s)", "[dry-run] " if self.dry_run else "",
                 c.rel, dest.relative_to(library), ident.confidence, ident.notes)
        if self.dry_run:
            return self._delete_source(c.path, c.rel)  # logs only
        try:
            self._copy_atomic(c.path, dest)
        except OSError as e:
            self._error(f"copy failed for {c.rel}: {e} (will retry next run)")
            return
        self._record(c.key, "filed", str(dest))
        self.notifier.add_filed(str(dest.relative_to(library)))
        self._delete_source(c.path, c.rel)  # only reached once the copy is verified

    # --------------------------------------------------------------- helpers
    def _copy_atomic(self, src: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.parent / f".{dest.name}.partial"  # dotfiles are ignored by Plex scans
        try:
            shutil.copyfile(src, tmp)
            if tmp.stat().st_size != src.stat().st_size:
                raise OSError("size mismatch after copy")
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)

    def _review(self, c: Candidate, ident: Identity | None, reason: str) -> None:
        log.warning("Review (%s): %s", reason, c.rel)
        if self.dry_run:
            return self._delete_source(c.path, c.rel)  # logs only
        batch = self.cfg.review_dir / time.strftime("%Y-%m-%d")
        batch.mkdir(parents=True, exist_ok=True)
        dest = batch / c.path.name
        n = 2  # a different file of the same name is already queued: don't clobber it
        while dest.exists() and dest.stat().st_size != c.size:
            dest = batch / f"{c.path.stem} ({n}){c.path.suffix}"
            n += 1
        try:
            if not dest.exists():
                self._copy_atomic(c.path, dest)
            (batch / f"{dest.name}.json").write_text(json.dumps({
                "source": c.rel, "reason": reason,
                "claude": ident.__dict__ if ident else None,
            }, indent=2))
        except OSError as e:
            self._error(f"could not quarantine {c.rel}: {e} (will retry next run)")
            return
        self._record(c.key, "review", str(dest))
        self.notifier.add_review(c.rel, reason)
        self._delete_source(c.path, c.rel)  # the review copy is now the only one

    # ------------------------------------------------------------- draining
    def _delete_source(self, path: Path, rel: str) -> None:
        """Delete a handled source file when this mode drains its source."""
        if not self._drain:
            return
        if self.dry_run:
            log.info("[dry-run] would delete source: %s", rel)
            return
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except OSError as e:
            self._error(f"could not delete source {rel}: {e} (will retry next run)")
            return
        log.info("Deleted source: %s", rel)
        self.stats["source_deleted"] += 1

    def _drain_handled(self, path: Path, rel: str, key: str) -> None:
        """A file the ledger already handled is still in a drained source."""
        row = self.ledger.get(key)
        status, dest = row if row else (None, None)
        if status in ("filed", "duplicate", "review") and not (dest and Path(dest).exists()):
            # The copy we recorded is gone: the source may be the only copy left.
            self._error(f"{rel} was {status} to {dest} but that file is missing; "
                        f"source kept (touch it to re-queue)")
            return
        self._delete_source(path, rel)

    def _prune_source(self, source: Path) -> None:
        """Remove release folders (and stray files) left holding only clutter.

        Anything still fresh, any video, and any file type not in
        leftover_extensions (e.g. .rar parts) keeps its folder.
        """
        if not source.is_dir():
            return
        cutoff = time.time() - self.cfg.age_minutes * 60

        def clutter(f: Path) -> bool:
            try:
                return (f.suffix.lower() in self.cfg.leftover_extensions
                        and f.lstat().st_mtime <= cutoff)
            except OSError:
                return False

        for dirpath, dirnames, filenames in os.walk(source, topdown=False):
            d = Path(dirpath)
            files = [d / f for f in filenames]
            if d == source:  # never remove the source itself, only stray clutter in it
                for f in filter(clutter, files):
                    try:
                        f.unlink()
                        log.info("Deleted leftover: %s", f.name)
                    except OSError as e:
                        self._error(f"could not delete leftover {f.name}: {e}")
                continue
            rel = d.relative_to(source).as_posix()
            if any((d / s).exists() or (d / s).is_symlink() for s in dirnames):
                continue  # a subfolder survived (bottom-up walk)
            if not all(clutter(f) for f in files):
                log.debug("Keeping source folder: %s", rel)
                continue
            try:
                for f in files:
                    f.unlink()
                d.rmdir()
            except OSError as e:
                self._error(f"could not remove source folder {rel}: {e}")
                continue
            log.info("Removed emptied source folder: %s", rel)

    def _record(self, key: str, status: str, dest: str | None = None) -> None:
        if not self.dry_run:
            self.ledger.record(key, status, dest)
        self.stats[status] += 1

    def _report_review_backlog(self) -> None:
        d = self.cfg.review_dir
        if d.is_dir():
            n = sum(1 for p in d.rglob("*") if p.is_file() and p.suffix != ".json")
            if n:
                log.warning("%d file(s) awaiting review in %s", n, d)

    def close(self) -> None:
        self.ledger.close()


def acquire_lock(path: Path):
    """Single-instance guard. flock is released by the kernel if we crash."""
    import fcntl  # POSIX only; imported here so the module still loads for tests

    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return None
    return f
