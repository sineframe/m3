"""CLI entry point that installs resolved settings before invoking pytest."""

from __future__ import annotations

import sys

from ._configuration_context import install_configuration, restore_configuration
from ._configuration_run import accept_configuration_transfer
from ._configuration_snapshot import ConfigurationSnapshotError


def main(arguments: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if arguments is None else arguments)
    if not args:
        print("m3 test: configuration transfer is missing", file=sys.stderr)
        return 2
    try:
        context = accept_configuration_transfer(args[0])
    except ConfigurationSnapshotError:
        print("m3 test: configuration transfer is unavailable", file=sys.stderr)
        return 2
    token = install_configuration(context)
    try:
        import pytest

        return int(pytest.main(args[1:]))
    finally:
        restore_configuration(token)
        if context.literal_registry is not None:
            context.literal_registry.close()


if __name__ == "__main__":
    raise SystemExit(main())
