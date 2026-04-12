"""Application configuration loaded from environment variables and .env file."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central settings for DocketMind.

    All values are loaded from environment variables or a .env file.
    Required fields (discord_bot_token, openai_api_key) must be present
    at startup — the application will fail fast if they are missing.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Discord
    discord_bot_token: str

    # OpenAI (LLM + embeddings share the same key)
    openai_api_key: str
    openai_llm_model: str = "gpt-4o"
    openai_embedding_model: str = "text-embedding-3-small"

    # Storage — all persistent state lives under data_dir
    data_dir: Path = Path("data")

    # RSS polling
    poll_interval_seconds: int = Field(default=900, ge=60)  # minimum 60 seconds

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


settings = Settings()  # pyright: ignore[reportCallIssue]
