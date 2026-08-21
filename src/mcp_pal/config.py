from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    anthropic_api_key: str | None = None
    openrouter_api_key: str | None = None
    claude_executable: str = "claude"
    claude_model_ids: list[str] | str = ["claude-sonnet-4-20250514"]
    opencode_api_key: str | None = None
    opencode_executable: str = "opencode"
    opencode_model_ids: list[str] | str = ["opencode/big-pickle"]
    database_path: str = "./mcp_pal.db"
    run_timeout_seconds: int = 120
    claude_max_turns: int = 5
    claude_max_budget_usd: float = 0.50
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="")

    def model_ids(self) -> list[str]:
        if isinstance(self.claude_model_ids, str):
            return [x.strip() for x in self.claude_model_ids.split(",") if x.strip()]
        return list(self.claude_model_ids)

    def opencode_models(self) -> list[str]:
        if isinstance(self.opencode_model_ids, str):
            return [x.strip() for x in self.opencode_model_ids.split(",") if x.strip()]
        return list(self.opencode_model_ids)

    def models_for(self, harness: str) -> list[str]:
        return self.opencode_models() if harness == "opencode" else self.model_ids()

    def opencode_providers(self) -> list[str]:
        return sorted({model.split("/", 1)[0].strip().lower() for model in self.opencode_models() if "/" in model})

    def opencode_provider_credentials(self) -> dict[str, str]:
        credentials = {
            "opencode": self.opencode_api_key,
            "opencode-go": self.opencode_api_key,
            "anthropic": self.anthropic_api_key,
            "openrouter": self.openrouter_api_key,
        }
        return {provider:key for provider,key in credentials.items() if key}

    @property
    def database_url(self) -> str:
        p = self.database_path
        return p if p.startswith("sqlite:") else f"sqlite:///{Path(p).expanduser()}"

@lru_cache
def get_settings() -> Settings:
    return Settings()
