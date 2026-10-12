import os
import sys
from pathlib import Path

# Lets eval tests (in hyphenated, non-package dirs) import the shared `_lib`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

SUITES = ("hard", "everyday", "all")
_SUITE_CACHE: dict[Path, str | None] = {}


def selected_suite() -> str:
    suite = (os.environ.get("MT_SUITE") or "hard").strip().lower()
    if suite not in SUITES:
        raise ValueError(f"unknown MT_SUITE {suite!r}; valid: {list(SUITES)}")
    return suite


def _task_suite(directory: Path) -> str | None:
    """The `suite:` field of a task folder's task.yaml, or None if it is not a task folder."""
    if directory not in _SUITE_CACHE:
        meta = directory / "task.yaml"
        value = None
        if meta.is_file():
            for line in meta.read_text(encoding="utf-8").splitlines():
                if line.startswith("suite:"):
                    value = line.split(":", 1)[1].strip()
                    break
        _SUITE_CACHE[directory] = value
    return _SUITE_CACHE[directory]


def pytest_ignore_collect(collection_path, config):
    """Collect only the task folders of the MT_SUITE suite (default `hard`); `_baseline` and
    `tests_offline` are never filtered here."""
    suite = (
        _task_suite(Path(collection_path)) if Path(collection_path).is_dir() else None
    )
    if suite is not None and selected_suite() != "all" and suite != selected_suite():
        return True
    return None


CONTRACT_VARIANTS = ["good", "naive", "good-D", "good-C", "good-S", "good-E", "good-P"]


def parse_variants(raw: str | None) -> list[str]:
    names = [v.strip() for v in (raw or "good").split(",") if v.strip()] or ["good"]
    try:
        from _server.surfaces import VARIANTS
    except Exception:
        VARIANTS = CONTRACT_VARIANTS
    bad = [v for v in names if v not in VARIANTS]
    if bad:
        raise ValueError(f"unknown MT_VARIANTS {bad}; valid: {list(VARIANTS)}")
    return names


def pytest_generate_tests(metafunc):
    if "variant" in metafunc.fixturenames:
        metafunc.parametrize(
            "variant",
            parse_variants(os.environ.get("MT_VARIANTS")),
            ids=lambda v: f"variant-{v}",
        )
