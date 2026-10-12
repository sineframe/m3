"""One actual process in a host queue; release is controlled by stdin."""

import asyncio
import sys
from pathlib import Path

from m3.harness.admission import HostAdmission
from m3.types import StartupAdmission


async def main() -> None:
    lease = await HostAdmission(Path(sys.argv[1])).acquire(
        StartupAdmission(enabled=True, slots=1, queue_timeout_seconds=10)
    )
    assert lease is not None
    try:
        print("admitted", flush=True)
        sys.stdin.readline()
    finally:
        lease.close()


asyncio.run(main())
