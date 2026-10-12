"""Use a preverified archive while exercising real native cache transactions.

Only the HTTP response is replaced. Production archive/member/binary checks,
owned process groups, publication and the native executable remain unchanged.
"""

from __future__ import annotations

import io
import runpy
import sys
from pathlib import Path

from m3 import _immutable_cache as cache

archive = Path(sys.argv[1]).read_bytes()


def open_archive(url: str, *, timeout: float) -> io.BytesIO:
    assert url == cache._URL
    assert timeout > 0
    return io.BytesIO(archive)


cache.urllib.request.urlopen = open_archive
sys.argv = [sys.argv[0], *sys.argv[2:]]
runpy.run_module("m3._preparation_worker", run_name="__main__")
