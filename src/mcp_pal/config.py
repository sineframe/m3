from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    anthropic_api_key: str | None = None
    claude_executable: str = "claude"
    claude_model_ids: list[str] | str = ["claude-sonnet-4-20250514"]
    database_path: str = "./mcp_pal.db"
    run_timeout_seconds: int = 120
    claude_max_turns: int = 5
    claude_max_budget_usd: float = 0.50
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="")

    def model_ids(self) -> list[str]:
        if isinstance(self.claude_model_ids, str):
            return [x.strip() for x in self.claude_model_ids.split(",") if x.strip()]
        return list(self.claude_model_ids)

    @property
    def database_url(self) -> str:
        p = self.database_path
        return p if p.startswith("sqlite:") else f"sqlite:///{Path(p).expanduser()}"

@lru_cache
def get_settings() -> Settings:
    return Settings()
