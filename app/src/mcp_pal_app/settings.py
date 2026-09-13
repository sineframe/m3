from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Credentials remain available to the application process, but must not
    # appear in Settings reprs or serialized configuration projections.
    anthropic_api_key: str | None = Field(default=None, repr=False, exclude=True)
    openrouter_api_key: str | None = Field(default=None, repr=False, exclude=True)
    claude_executable: str = "claude"
    claude_model_ids: list[str] | str = ["claude-sonnet-4-20250514"]
    opencode_api_key: str | None = Field(default=None, repr=False, exclude=True)
    opencode_executable: str = "opencode"
    opencode_model_ids: list[str] | str = ["opencode/big-pickle"]
    database_path: str = "./mcp_pal.db"
    run_timeout_seconds: int = 120
    claude_max_turns: int = 5
    claude_max_budget_usd: float = 0.50
    # Application callers may supply a selected env file explicitly through
    # Pydantic settings; the SDK/application default must not read cwd/.env.
    model_config = SettingsConfigDict(env_file=None, extra="ignore", env_prefix="")

    @classmethod
    def from_env_file(cls, path: str | Path) -> "Settings":
        """Load settings from one explicitly selected env file.

        Ambient environment variables retain higher precedence through
        pydantic-settings.  The default constructor never reads cwd ``.env``;
        this method is the application boundary for an explicitly selected
        file.  Errors deliberately omit the path and file contents.
        """

        try:
            selected = Path(path)
            if not selected.is_file():
                raise ValueError("environment file is unavailable")
            # pydantic-settings accepts this runtime-only constructor option,
            # but its generated typing does not expose it.
            return cls(_env_file=selected)  # type: ignore[call-arg]
        except (OSError, TypeError, ValueError):
            raise ValueError("could not load the selected environment file") from None

    def model_ids(self) -> list[str]:
        if isinstance(self.claude_model_ids, str):
            return [x.strip() for x in self.claude_model_ids.split(",") if x.strip()]
        return list(self.claude_model_ids)

    def opencode_models(self) -> list[str]:
        if isinstance(self.opencode_model_ids, str):
            return [x.strip() for x in self.opencode_model_ids.split(",") if x.strip()]
        return list(self.opencode_model_ids)

    def models_for(self, harness: str) -> list[str]:
        if harness == "acp":
            return ["agent-default"]
        return self.opencode_models() if harness == "opencode" else self.model_ids()

    def opencode_providers(self) -> list[str]:
        return sorted(
            {
                model.split("/", 1)[0].strip().lower()
                for model in self.opencode_models()
                if "/" in model
            }
        )

    def opencode_provider_credentials(self) -> dict[str, str]:
        credentials = {
            "opencode": self.opencode_api_key,
            "opencode-go": self.opencode_api_key,
            "anthropic": self.anthropic_api_key,
            "openrouter": self.openrouter_api_key,
        }
        return {provider: key for provider, key in credentials.items() if key}

    @property
    def database_url(self) -> str:
        p = self.database_path
        return p if p.startswith("sqlite:") else f"sqlite:///{Path(p).expanduser()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
