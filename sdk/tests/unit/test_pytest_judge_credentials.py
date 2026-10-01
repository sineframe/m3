import os
from types import SimpleNamespace

import pytest

from m3.pytest_plugin import (
    _parse_credential_mappings,
    pytest_configure,
    pytest_unconfigure,
)


def test_judge_credential_mapping_is_restored_after_direct_pytest(monkeypatch):
    monkeypatch.setenv("MY_JUDGE_KEY", "source-secret")
    monkeypatch.setenv("M3_JUDGE_API_KEY", "original-secret")

    class Config:
        def getoption(self, name):
            return {
                "--credential-env": ["judge:M3_JUDGE_API_KEY=MY_JUDGE_KEY"],
            }.get(name)

        def addinivalue_line(self, *_args):
            pass

    config = Config()
    pytest_configure(config)
    try:
        assert os.environ["M3_JUDGE_API_KEY"] == "source-secret"
    finally:
        pytest_unconfigure(config)
    assert os.environ["M3_JUDGE_API_KEY"] == "original-secret"


@pytest.mark.parametrize(
    "raw",
    [
        "TOKEN=M3_ACCESS_TOKEN",
        "M3_ACCESS_TOKEN=SOURCE",
        "codex:TOKEN=M3_ACCESS_TOKEN",
        "judge:M3_ACCESS_TOKEN=SOURCE",
        " pi : TOKEN = M3_ACCESS_TOKEN ",
        " judge : M3_ACCESS_TOKEN = SOURCE ",
    ],
)
def test_direct_pytest_reserves_upload_token_name(raw):
    config = SimpleNamespace(getoption=lambda _name: [raw])
    with pytest.raises(pytest.UsageError, match="cannot be mapped"):
        _parse_credential_mappings(config)


def test_direct_pytest_allows_ordinary_global_scoped_and_judge_names():
    config = SimpleNamespace(
        getoption=lambda _name: [
            "TOKEN=SOURCE",
            "codex:VENDOR_KEY=VENDOR_SOURCE",
            "judge:M3_JUDGE_API_KEY=JUDGE_SOURCE",
        ]
    )
    assert _parse_credential_mappings(config) == (
        {"TOKEN": "SOURCE"},
        {"codex": {"VENDOR_KEY": "VENDOR_SOURCE"}},
        {"M3_JUDGE_API_KEY": "JUDGE_SOURCE"},
    )
