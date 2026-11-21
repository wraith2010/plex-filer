"""Movie source draining: handled movies leave the source; TV never does."""
import os
import time
from pathlib import Path

import pytest

from plex_filer import filer as filer_mod
from plex_filer.config import Config
from plex_filer.identify import Identity

MB = 1024 * 1024
OLD = time.time() - 3 * 3600


def make(path: Path, size: int = 60 * MB, old: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.truncate(size)  # sparse: cheap even at movie-ish sizes
    if old:
        os.utime(path, (OLD, OLD))
    return path


class FakeIdentifier:
    """Answers by filename: {name: Identity kwargs}; unknown names get no answer."""

    def __init__(self, answers):
        self.answers = answers

    def identify(self, mode, paths, known):
        out = {}
        for i, p in enumerate(paths):
            a = self.answers.get(Path(p).name)
            if a:
                out[i] = Identity(**({"id": i, "season": None, "episode": None,
                                      "episode_end": None, "notes": "test"} | a))
        return out


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(filer_mod, "Identifier", lambda *a: None)
    cfg = Config(
        tv_source=tmp_path / "stage/tv", tv_library=tmp_path / "plex/tv",
        movie_source=tmp_path / "stage/movies", movie_library=tmp_path / "plex/movies",
        review_dir=tmp_path / "stage/review", state_dir=tmp_path / "state",
        min_video_mb=50,
    )
    for d in (cfg.tv_source, cfg.tv_library, cfg.movie_source, cfg.movie_library):
        d.mkdir(parents=True)

    def run(answers, dry_run=False):
        f = filer_mod.Filer(cfg, dry_run=dry_run)
        f.ident = FakeIdentifier(answers)
        try:
            f.run()
        finally:
            f.close()
        return f

    return cfg, run


def movie(title, year=2010, conf=0.95):
    return {"kind": "movie", "title": title, "year": year, "confidence": conf}


def test_filed_movie_is_removed_with_its_release_folder(env):
    cfg, run = env
    rel = cfg.movie_source / "Inception.2010.1080p"
    make(rel / "Inception.2010.1080p.mkv")
    make(rel / "Inception.2010.1080p.nfo", 1024)
    make(rel / "Sample" / "sample-inception.mkv", 5 * MB)
    make(rel / "Subs" / "English.srt", 1024)

    f = run({"Inception.2010.1080p.mkv": movie("Inception")})

    assert (cfg.movie_library / "Inception (2010).mkv").stat().st_size == 60 * MB
    assert list(cfg.movie_source.iterdir()) == []
    assert f.stats["filed"] == 1 and f.stats["error"] == 0


def test_duplicate_review_and_junk_are_removed(env):
    cfg, run = env
    make(cfg.movie_library / "Heat (1995).mkv")
    make(cfg.movie_source / "Heat.1995.mkv")
    make(cfg.movie_source / "Mystery.Thing.mkv")
    make(cfg.movie_source / "Trailer.mkv")

    f = run({
        "Heat.1995.mkv": movie("Heat", 1995),
        "Mystery.Thing.mkv": movie("Mystery", conf=0.3),
        "Trailer.mkv": {"kind": "junk", "title": None, "year": None, "confidence": 0.9},
    })

    assert list(cfg.movie_source.iterdir()) == []
    assert f.stats["duplicate"] == 1 and f.stats["review"] == 1 and f.stats["junk"] == 1
    review = list(cfg.review_dir.rglob("Mystery.Thing.mkv"))
    assert len(review) == 1 and review[0].stat().st_size == 60 * MB


def test_failed_copy_keeps_source(env, monkeypatch):
    cfg, run = env
    src = make(cfg.movie_source / "Up.2009.mkv")

    def boom(self, s, d):
        raise OSError("disk full")
    monkeypatch.setattr(filer_mod.Filer, "_copy_atomic", boom)

    f = run({"Up.2009.mkv": movie("Up", 2009)})
    assert src.exists() and f.stats["error"] == 1


def test_fresh_and_unknown_content_is_left_alone(env):
    cfg, run = env
    fresh = make(cfg.movie_source / "Downloading" / "Movie.mkv", old=False)
    rar = make(cfg.movie_source / "Packed" / "movie.rar")
    make(cfg.movie_source / "Packed" / "movie.nfo", 1024)

    run({})
    assert fresh.exists() and rar.exists()
    assert (cfg.movie_source / "Packed" / "movie.nfo").exists()


def test_previously_filed_movie_is_cleared_on_next_run(env):
    cfg, run = env
    src = make(cfg.movie_source / "Up.2009.mkv")
    run({"Up.2009.mkv": movie("Up", 2009)}, dry_run=False)
    assert not src.exists()

    # Simulate a file that was filed by an older (non-draining) run.
    src = make(cfg.movie_source / "Up.2009.mkv")
    run({})
    assert not src.exists()


def test_previously_filed_movie_with_missing_copy_is_kept(env):
    cfg, run = env
    src = make(cfg.movie_source / "Up.2009.mkv")
    run({"Up.2009.mkv": movie("Up", 2009)})
    (cfg.movie_library / "Up (2009).mkv").unlink()

    src = make(cfg.movie_source / "Up.2009.mkv")
    f = run({})
    assert src.exists() and f.stats["error"] == 1


def test_dry_run_deletes_nothing(env):
    cfg, run = env
    src = make(cfg.movie_source / "Up.2009.mkv")
    run({"Up.2009.mkv": movie("Up", 2009)}, dry_run=True)
    assert src.exists()


def test_review_name_collision_does_not_lose_file(env):
    cfg, run = env
    make(cfg.movie_source / "a" / "movie.mkv", 60 * MB)
    make(cfg.movie_source / "b" / "movie.mkv", 70 * MB)

    run({})  # no answer from Claude -> both go to review
    sizes = sorted(p.stat().st_size for p in cfg.review_dir.rglob("*.mkv"))
    assert sizes == [60 * MB, 70 * MB]
    assert list(cfg.movie_source.iterdir()) == []


def test_tv_source_is_never_drained(env):
    cfg, run = env
    ep = make(cfg.tv_source / "The.Boys.S01E01.mkv")
    run({"The.Boys.S01E01.mkv": {"kind": "episode", "title": "The Boys", "year": 2019,
                                 "season": 1, "episode": 1, "confidence": 0.95}})
    assert (cfg.tv_library / "The Boys (2019)").is_dir()
    assert ep.exists()
