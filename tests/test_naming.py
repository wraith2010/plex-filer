from pathlib import Path

from plex_filer.naming import (LibraryIndex, movie_dest, norm, sanitize,
                               tv_dest, with_year)

LIB = Path("/mnt/plex/tvshows")


def test_sanitize():
    assert sanitize("Mission: Impossible") == "Mission - Impossible"
    assert sanitize('What/If? "Now"') == "WhatIf Now"
    assert sanitize("Trailing dot...") == "Trailing dot"


def test_movie_is_flat():
    d = movie_dest(Path("/mnt/plex/movies"), "Dune: Part Two", 2024, ".MKV")
    assert d == Path("/mnt/plex/movies/Dune - Part Two (2024).mkv")


def test_tv_dest_matches_existing_layout():
    d = tv_dest(LIB, "The Boys", 5, 4, None, ".mkv", pad_season=False)
    assert d == LIB / "The Boys" / "Season 5" / "The Boys - S05E04.mkv"


def test_multi_episode_and_padded_season():
    d = tv_dest(LIB, "Show (2020)", 1, 1, 2, ".mkv", pad_season=True)
    assert d.parent.name == "Season 01"
    assert d.name == "Show (2020) - S01E01-E02.mkv"


def test_norm_ignores_year_the_punctuation():
    assert norm("The Pitt") == norm("pitt") == norm("The Pitt (2025)")


def test_resolve_reuses_existing_folder():
    idx = LibraryIndex(["Invincible (2021)", "The Boys", "FROM"])
    assert idx.resolve("Invincible", None) == "Invincible (2021)"
    assert idx.resolve("Invincible", 2021) == "Invincible (2021)"
    assert idx.resolve("the boys", 2019) == "The Boys"  # yearless folder wins over a new one
    assert idx.resolve("From", None) == "FROM"


def test_resolve_remake_gets_own_folder():
    idx = LibraryIndex(["Battlestar Galactica (1978)"])
    assert idx.resolve("Battlestar Galactica", 2004) == "Battlestar Galactica (2004)"


def test_with_year():
    assert with_year("Taskmaster", None) == "Taskmaster"
    assert with_year("Jury Duty (2023)", 2023) == "Jury Duty (2023)"
