"""Fast access to the SDK's terminal styling for the CLI.

``m3/__init__.py`` imports the whole SDK, which takes a noticeable time (and
several seconds on a busy machine). The banner must appear as soon as the
user presses enter, so the CLI loads ``m3/_terminal.py`` (standard library
only) directly, without running the package ``__init__``. It is registered
under its usual name, so a later ``import m3`` reuses the same module.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

_NAME = "m3._terminal"


def _load() -> ModuleType:
    loaded = sys.modules.get(_NAME)
    if loaded is not None:
        return loaded
    package = importlib.util.find_spec("m3")
    locations = list(package.submodule_search_locations or ()) if package else []
    source = Path(locations[0]) / "_terminal.py" if locations else None
    # Installs without .py sources (compiled-only, zip) use the normal import.
    spec = (
        importlib.util.spec_from_file_location(_NAME, source)
        if source is not None and source.is_file()
        else None
    )
    if spec is None or spec.loader is None:
        return importlib.import_module(_NAME)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_NAME] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(_NAME, None)
        raise
    return module


if TYPE_CHECKING:
    from m3._terminal import (
        GRADIENT,
        Style,
        fit,
        hyperlink,
        stream_is_utf8,
        supports_hyperlinks,
    )
else:
    _module = _load()
    GRADIENT = _module.GRADIENT
    Style = _module.Style
    fit = _module.fit
    hyperlink = _module.hyperlink
    stream_is_utf8 = _module.stream_is_utf8
    supports_hyperlinks = _module.supports_hyperlinks

__all__ = [
    "GRADIENT",
    "Style",
    "fit",
    "hyperlink",
    "stream_is_utf8",
    "supports_hyperlinks",
]
