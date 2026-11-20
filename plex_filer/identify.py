"""Ask Claude to identify media from release-style paths."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import anthropic

SYSTEM = """You identify TV episodes and movies from messy download paths so they can be filed in a Plex library.

You receive a list of items. Each has an id and a path RELATIVE to a download folder (folder names are often the only place the show name appears). Ignore the download folder itself.

Rules:
- Strip release junk: resolution, source, codec, audio, release group, site tags like [EZTVx.to].
- "title" is the real, properly capitalised, properly spaced and punctuated title (e.g. "the.boys" -> "The Boys", "DONT LEAVE ME" style noise is NOT part of the show title).
- If a known library name is given that is clearly the same show/movie, reuse its title exactly (without the year suffix). Known names are formatted "Title (Year)" or "Title".
- "year": the release/first-air year. Use a year present in the path or in a matching known library name. If neither exists, give your best knowledge only if you are confident, otherwise null. Never invent a year.
- For TV, season and episode are integers. Double episodes (S01E01E02, S01E01-E02) -> episode and episode_end. Daily/date-based shows without S/E numbers: kind="unknown".
- kind="junk" for samples, trailers, extras, featurettes, or anything that is not the main episode/movie.
- kind="unknown" if you cannot tell what it is. Do not guess.
- confidence is 0.0-1.0: how sure you are that title, year, season, and episode are all correct. Lower it when you are relying on memory rather than the path, or when the title is ambiguous (remakes, same-name shows).
- Return exactly one result per input id."""

TOOL = {
    "name": "report",
    "description": "Report the identification of every input item.",
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "kind": {"type": "string", "enum": ["episode", "movie", "junk", "unknown"]},
                        "title": {"type": ["string", "null"]},
                        "year": {"type": ["integer", "null"]},
                        "season": {"type": ["integer", "null"]},
                        "episode": {"type": ["integer", "null"]},
                        "episode_end": {"type": ["integer", "null"]},
                        "confidence": {"type": "number"},
                        "notes": {"type": "string", "description": "One short sentence of reasoning."},
                    },
                    "required": ["id", "kind", "confidence", "notes"],
                },
            }
        },
        "required": ["results"],
    },
}


@dataclass
class Identity:
    id: int
    kind: str
    title: str | None
    year: int | None
    season: int | None
    episode: int | None
    episode_end: int | None
    confidence: float
    notes: str

    @classmethod
    def from_raw(cls, r: dict[str, Any]) -> "Identity":
        def i(v: Any) -> int | None:
            return v if isinstance(v, int) and not isinstance(v, bool) else None

        return cls(
            id=int(r["id"]),
            kind=r.get("kind", "unknown"),
            title=(r.get("title") or None),
            year=i(r.get("year")),
            season=i(r.get("season")),
            episode=i(r.get("episode")),
            episode_end=i(r.get("episode_end")),
            confidence=float(r.get("confidence", 0.0)),
            notes=str(r.get("notes", "")),
        )


class Identifier:
    def __init__(self, model: str, api_key_env: str):
        key = os.environ.get(api_key_env)
        if not key:
            raise RuntimeError(f"{api_key_env} is not set")
        self.client = anthropic.Anthropic(api_key=key, max_retries=4, timeout=120)
        self.model = model

    def identify(self, mode: str, paths: list[str], known: list[str]) -> dict[int, Identity]:
        """mode is 'tv' or 'movie' (a hint). Returns {index_in_paths: Identity}.
        Raises on API failure; callers must treat that as 'retry next run'."""
        items = "\n".join(f"{i}: {p}" for i, p in enumerate(paths))
        known_txt = "\n".join(known) if known else "(none)"
        user = (
            f"These downloads are expected to be: {'TV episodes' if mode == 'tv' else 'movies'}.\n\n"
            f"Known library names:\n{known_txt}\n\nItems:\n{items}"
        )
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM,
            tools=[TOOL],
            tool_choice={"type": "tool", "name": "report"},
            messages=[{"role": "user", "content": user}],
        )
        out: dict[int, Identity] = {}
        for block in resp.content:
            if block.type == "tool_use" and block.name == "report":
                for r in block.input.get("results", []):
                    try:
                        ident = Identity.from_raw(r)
                    except (KeyError, ValueError, TypeError):
                        continue
                    if 0 <= ident.id < len(paths):
                        out[ident.id] = ident
        return out
