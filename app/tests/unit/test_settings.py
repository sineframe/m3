"""Application settings boundary tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_pal_app.settings import Settings


def test_application_settings_no_longer_loads_cwd_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "DATABASE_PATH=leaked-from-dotenv.sqlite\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_PATH", raising=False)

    assert Settings().database_path == "./mcp_pal.db"


def test_application_credentials_are_process_local_not_serialized() -> None:
    settings = Settings(
        anthropic_api_key="anthropic-secret",
        openrouter_api_key="openrouter-secret",
        opencode_api_key="opencode-secret",
    )

    assert settings.anthropic_api_key == "anthropic-secret"
    assert settings.opencode_provider_credentials()["anthropic"] == "anthropic-secret"
    representation = repr(settings)
    encoded = settings.model_dump_json()
    assert "anthropic-secret" not in representation
    assert "openrouter-secret" not in representation
    assert "opencode-secret" not in representation
    assert "anthropic-secret" not in encoded
    assert "openrouter-secret" not in encoded
    assert "opencode-secret" not in encoded
    assert "anthropic_api_key" not in settings.model_dump()


def test_settings_from_env_file_is_explicit_and_ambient_env_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "selected.env"
    selected.write_text(
        "ANTHROPIC_API_KEY=file-secret\nDATABASE_PATH=file.sqlite\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("DATABASE_PATH=implicit.sqlite\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_PATH", "ambient.sqlite")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    settings = Settings.from_env_file(selected)

    assert settings.anthropic_api_key == "file-secret"
    assert settings.database_path == "ambient.sqlite"


def test_settings_from_env_file_errors_are_generic(tmp_path: Path) -> None:
    missing = tmp_path / "missing-secret.env"
    with pytest.raises(
        ValueError, match=r"^could not load the selected environment file$"
    ) as caught:
        Settings.from_env_file(missing)
    assert str(missing) not in str(caught.value)

    invalid = tmp_path / "invalid.env"
    invalid.write_text("RUN_TIMEOUT_SECONDS=not-a-number\n", encoding="utf-8")
    with pytest.raises(
        ValueError, match=r"^could not load the selected environment file$"
    ) as caught:
        Settings.from_env_file(invalid)
    assert "not-a-number" not in str(caught.value)
