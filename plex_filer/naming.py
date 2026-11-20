"""Deterministic path building and library-folder matching. No Claude here."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_ILLEGAL = re.compile(r'[<>"/\\|?*\x00-\x1f]')
_YEAR_SUFFIX = re.compile(r"\s*\((\d{4})\)\s*$")


def sanitize(name: str) -> str:
    """Make a string safe as a single path component on Linux/SMB/NTFS shares."""
    name = name.replace(":", " -")
    name = _ILLEGAL.sub("", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name.rstrip(". ")  # trailing dots/spaces break SMB


def with_year(title: str, year: int | None) -> str:
    title = sanitize(title)
    if year and not _YEAR_SUFFIX.search(title):
        return f"{title} ({year})"
    return title


def norm(title: str) -> str:
    """Comparison key: lowercase alphanumerics only, trailing '(year)' removed."""
    t = _YEAR_SUFFIX.sub("", title)
    t = t.lower().replace("&", "and")
    t = re.sub(r"^the\s+", "", t)
    return re.sub(r"[^a-z0-9]", "", t)


@dataclass
class LibraryIndex:
    """Existing folder names in a library, so new files join them instead of
    spawning near-duplicate folders ('Invincible' vs 'Invincible (2021)')."""

    names: list[str]

    @classmethod
    def from_dir(cls, root: Path) -> "LibraryIndex":
        if not root.is_dir():
            return cls([])
        return cls(sorted(p.name for p in root.iterdir() if p.is_dir()))

    def resolve(self, title: str, year: int | None) -> str:
        """Return the folder name to use for (title, year)."""
        key = norm(title)
        matches = [n for n in self.names if norm(n) == key]
        if matches:
            if year:
                for n in matches:  # same-title remakes: prefer the matching year
                    m = _YEAR_SUFFIX.search(n)
                    if m and int(m.group(1)) == year:
                        return n
                # no year match: only reuse a yearless folder, else a new remake folder
                plain = [n for n in matches if not _YEAR_SUFFIX.search(n)]
                if plain:
                    return plain[0]
                return with_year(title, year)
            return matches[0]
        return with_year(title, year)


def episode_tag(season: int, episode: int, episode_end: int | None) -> str:
    tag = f"S{season:02d}E{episode:02d}"
    if episode_end and episode_end > episode:
        tag += f"-E{episode_end:02d}"
    return tag


def tv_dest(library: Path, folder: str, season: int, episode: int,
            episode_end: int | None, ext: str, pad_season: bool) -> Path:
    season_dir = f"Season {season:02d}" if pad_season else f"Season {season}"
    fname = f"{folder} - {episode_tag(season, episode, episode_end)}{ext.lower()}"
    return library / folder / season_dir / fname


def movie_dest(library: Path, title: str, year: int | None, ext: str) -> Path:
    """Movies are flat in the library: 'Title (Year).ext', no subfolder."""
    return library / f"{with_year(title, year)}{ext.lower()}"
