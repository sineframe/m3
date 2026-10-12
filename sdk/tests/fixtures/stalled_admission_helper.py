"""Block one real queue mkdir until released, then execute the real helper."""

import os
import runpy
import sys

helper, marker, gate = sys.argv[1:4]
sys.argv = [helper, *sys.argv[4:]]
original_mkdir = os.mkdir
blocked = False


def delayed_mkdir(*args: object, **kwargs: object) -> None:
    global blocked
    if not blocked:
        blocked = True
        with open(marker, "w") as evidence:
            evidence.write("queue filesystem blocked")
        with open(gate, "rb", buffering=0) as release:
            release.read(1)
    original_mkdir(*args, **kwargs)


os.mkdir = delayed_mkdir
runpy.run_path(helper, run_name="__main__")
