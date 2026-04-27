"""Application configuration loaded from environment variables and .env file."""

from pathlib import Path
from typing import Any, Literal

from pydantic import Field
from pydantic_settings import BaseSettings as BaseConfig
from pydantic_settings import SettingsConfigDict as ModelConfigDict


class Config(BaseConfig):
    """Runtime configuration loaded from environment variables and `.env`.

    Required credentials must be set at startup.
    """

    model_config = ModelConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Discord
    discord_bot_token: str = ""
    discord_guild_id: int | None = None  # set for instant guild sync in dev; None = global sync

    # Slack (socket mode)
    slack_bot_token: str = ""  # xoxb-...
    slack_app_token: str = ""  # xapp-... (required for socket mode)

    # LLM
    llm_provider: Literal["openai", "anthropic", "mock"] = "openai"
    llm_model: str = "gpt-5.4"
    llm_api_key: str
    llm_extra: dict[str, Any] = {}

    # Embeddings
    embed_provider: Literal["openai", "mock"] = "openai"
    embed_model: str = "text-embedding-3-small"
    embed_api_key: str
    embed_extra: dict[str, Any] = {}

    # Storage — all persistent state lives under data_dir
    data_dir: Path = Path("data")

    # RAG
    chunk_size: int = 1024
    chunk_overlap: int = 200
    similarity_top_k: int = 5

    # RSS polling
    poll_interval_seconds: int = Field(default=600, ge=60)  # minimum 60 seconds

    # Logging
    log_level: str = "INFO"

    @property
    def db_path(self) -> Path:
        """Absolute path to the SQLite database file."""
        return self.data_dir / "docketmind.db"

    @property
    def index_path(self) -> Path:
        """Absolute path to the LlamaIndex vector store persistence directory."""
        return self.data_dir / "index"

    @property
    def pdfs_path(self) -> Path:
        """Absolute path to the directory where downloaded PDFs are stored."""
        return self.data_dir / "pdfs"


settings = Config()  # type: ignore[call-arg]
