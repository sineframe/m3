import os

from m3.pytest_plugin import pytest_configure, pytest_unconfigure


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
