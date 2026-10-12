"""Real controller-loss fixture; native writer ignores TERM until group KILL."""

import asyncio
import sys
from pathlib import Path

from m3.harness.admission import HostAdmission
from m3.harness.guardian import NativeGuardian
from m3.types import StartupAdmission


async def main() -> None:
    root = Path(sys.argv[1])
    settings = StartupAdmission(enabled=True, slots=1, queue_timeout_seconds=5)
    lease = await HostAdmission(root).acquire(settings)
    assert lease is not None
    guardian = await NativeGuardian.spawn(
        lease,
        [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "print('writer-ready',flush=True); time.sleep(60)",
        ],
        {},
        root.parent,
        limit=1024 * 1024,
    )
    assert guardian.process.stdout is not None
    assert await guardian.process.stdout.readline() == b"writer-ready\n"
    print(guardian.native_pid, flush=True)
    await asyncio.sleep(60)


asyncio.run(main())
