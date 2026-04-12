"""Ingestion package: RSS polling, PDF downloading, embedding, and memory updates."""

from docketmind.ingestion.pipeline import SyncResult, sync_case
from docketmind.ingestion.scheduler import add_case, remove_case, start

__all__ = ["sync_case", "SyncResult", "start", "add_case", "remove_case"]
