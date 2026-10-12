"""Private helper with controlled status failures for lifecycle regressions."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import m3._process_group_custody as custody
from m3._preparation_worker import _supervise

mode = sys.argv[1]
recover = Path(sys.argv[2])
query_started = Path(sys.argv[3])
original_inventory = custody._inventory
inventory_calls = 0


def inventory() -> dict[int, tuple[int, bytes]]:
    global inventory_calls
    inventory_calls += 1
    if inventory_calls > 1 and not recover.exists():
        if mode == "invalid-owner":
            return {}
        if mode == "missing-leader":
            return {os.getpid(): original_inventory()[os.getpid()]}
    if mode == "startup" and not recover.exists():
        raise custody.ProcessStatusUnavailable("inventory unavailable")
    entries = original_inventory()
    if mode == "startup":
        query_started.touch()
    return entries


custody._inventory = inventory
if mode != "startup":
    custody.validate_process_status()

original_collector = custody.InventoryCollector


class Collector(original_collector):
    failed_once = False

    def read(self) -> dict[int, tuple[int, bytes]]:
        if mode == "startup" and not recover.exists():
            raise custody.ProcessStatusUnavailable("inventory unavailable")
        if mode == "transient" and not self.failed_once:
            self.failed_once = True
            raise custody.ProcessStatusUnavailable("interrupted inventory")
        if mode == "persistent" and not recover.exists():
            raise custody.ProcessStatusUnavailable("inventory unavailable")
        entries = super().read()
        if mode == "startup":
            query_started.touch()
        return entries


custody.InventoryCollector = Collector
# _supervise imported the class before the injection above.
import m3._preparation_worker as worker

worker.InventoryCollector = Collector
original_validation = worker.validate_process_status


def preflight() -> None:
    if mode == "startup" and recover.exists():
        raise AssertionError("recovery performed a second synchronous status probe")
    original_validation()


worker.validate_process_status = preflight

if mode in {"slow", "exit", "malformed", "missing-owner", "stale"}:
    original_popen = subprocess.Popen
    intercepted = False
    queries = 0

    def launch(argv, *args, **kwargs):
        global intercepted, queries
        if argv[0] == "/bin/ps":
            queries += 1
            with query_started.open("a") as stream:
                stream.write(str(time.monotonic()) + "\n")
            if not intercepted:
                intercepted = True
                programs = {
                    "slow": "import time; time.sleep(5)",
                    "exit": "raise SystemExit(1)",
                    "malformed": "print('bad row')",
                    "missing-owner": "print('999999 999999 Z')",
                }
                if mode == "stale":
                    captured = query_started.with_suffix(".captured")
                    program = f"""
import subprocess
import sys
import time
from pathlib import Path

data = subprocess.check_output(['/bin/ps', '-axo', 'pid=,pgid=,stat='])
Path({str(captured)!r}).touch()
while not Path({str(recover)!r}).exists():
    time.sleep(.005)
sys.stdout.buffer.write(data)
"""
                else:
                    program = programs[mode]
                argv = [sys.executable, "-c", program]
            elif mode == "stale" and queries in {2, 3}:
                argv = [sys.executable, "-c", "raise SystemExit(1)"]
        return original_popen(argv, *args, **kwargs)

    subprocess.Popen = launch

parser = argparse.ArgumentParser()
parser.add_argument("--max-jobs", type=int, default=64)
parser.add_argument("--root", type=Path)
parser.add_argument("--ownership-fd", type=int)
arguments = parser.parse_args(sys.argv[4:])
_supervise(arguments.max_jobs, arguments.root, arguments.ownership_fd)
