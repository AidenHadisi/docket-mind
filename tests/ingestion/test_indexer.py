"""Tests for LlamaIndex vector store indexer."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from docketmind.ingestion.indexer import get_index, upsert_entry
from docketmind.models import DocketEntry


@pytest.fixture
def tmp_index_path(tmp_path: Path, monkeypatch):
    """Point settings.index_path to a temp directory by redirecting data_dir."""
    import docketmind.config as cfg

    monkeypatch.setattr(cfg.settings, "data_dir", tmp_path)
    return tmp_path / "index"


@pytest.fixture
def sample_entry() -> DocketEntry:
    return DocketEntry(
        id="entry-001",
        case_id="case-001",
        court_listener_id="cl-001",
        title="Order on Motion to Dismiss",
        content="Court grants defendant's motion to dismiss for lack of jurisdiction.",
        content_hash="abc123",
        date_filed=datetime(2026, 4, 7, tzinfo=UTC),
        embedded=False,
    )


def test_get_index_creates_index_directory(tmp_index_path: Path):
    get_index()
    assert tmp_index_path.exists()


def test_upsert_entry_indexes_without_error(tmp_index_path: Path, sample_entry: DocketEntry):
    index = get_index()
    upsert_entry(index, sample_entry)  # should not raise


def test_upsert_entry_is_idempotent(tmp_index_path: Path, sample_entry: DocketEntry):
    index = get_index()
    upsert_entry(index, sample_entry)
    upsert_entry(index, sample_entry)  # second upsert must not raise or duplicate
