"""Root test configuration — sets required environment variables before any docketmind import."""

import os

# Provide dummy values for required settings so pydantic-settings doesn't
# raise a ValidationError during collection.  Tests that actually need these
# services should override them or mock the relevant clients.
os.environ.setdefault("DISCORD_BOT_TOKEN", "test-discord-token")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
