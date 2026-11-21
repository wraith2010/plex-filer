from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Config:
    # Claude
    model: str = "claude-haiku-5-5"
    api_key_env: str = "ANTHROPIC_API_KEY"
    batch_size: int = 20
    min_confidence: float = 0.8

    # Paths
    tv_source: Path = Path("/data/stage/tvshows")
    tv_library: Path = Path("/mnt/plex/tvshows")
    movie_source: Path = Path("/data/stage/movies")
    movie_library: Path = Path("/mnt/plex/movies")
    review_dir: Path = Path("/data/stage/review")
    state_dir: Path = Path("/var/lib/plex-filer")

    # Email notification (credentials come from env: SMTP_USER / SMTP_PASSWORD)
    notify_enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    mail_from: str = ""  # blank = same as SMTP_USER
    mail_to: str = ""

    # Behaviour
    age_minutes: int = 60
    min_video_mb: int = 50
    pad_season_folder: bool = False  # "Season 4" (False, matches current library) vs "Season 04"
    video_extensions: tuple[str, ...] = (".mkv", ".mp4", ".m4v", ".avi", ".ts")
    sample_markers: tuple[str, ...] = ("sample",)

    # Movie source draining (TV is never drained: its source is the seeding dir).
    # When on, every movie the filer has dealt with (filed, duplicate, sent to
    # review, junk) is deleted from movie_source, and release folders left with
    # only clutter are removed.
    delete_movie_source: bool = True
    # Non-video files that may be deleted along with an emptied release folder.
    # A folder holding anything else (e.g. .rar parts) is left alone.
    leftover_extensions: tuple[str, ...] = (
        ".nfo", ".txt", ".srt", ".sub", ".idx", ".ass", ".ssa", ".sfv", ".md5",
        ".jpg", ".jpeg", ".png", ".gif", ".url", ".nzb", ".exe", ".parts",
    )

    @property
    def db_path(self) -> Path:
        return self.state_dir / "ledger.db"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / "plex-filer.lock"


def load_config(path: Path | None) -> Config:
    if path is None:
        return Config()
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    flat: dict = {}
    for section in raw.values():
        if isinstance(section, dict):
            flat.update(section)
        # top-level keys are ignored; everything lives in a [section]

    valid = Config.__dataclass_fields__
    unknown = set(flat) - set(valid)
    if unknown:
        raise ValueError(f"Unknown config keys: {sorted(unknown)}")

    for k, v in list(flat.items()):
        if k.endswith(("_source", "_library")) or k in ("review_dir", "state_dir"):
            flat[k] = Path(v)
        elif k in ("video_extensions", "sample_markers", "leftover_extensions"):
            flat[k] = tuple(s.lower() for s in v)
    return Config(**flat)
