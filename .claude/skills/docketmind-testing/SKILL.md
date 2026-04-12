---
name: docketmind-testing
description: Use when writing, editing, or reviewing tests in the DocketMind project. Covers the full idiomatic testing stack, fixture conventions, mocking patterns, and anti-patterns to avoid.
---

# DocketMind Testing

## Stack

| Tool | Purpose |
|---|---|
| `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`) | All tests; no `@pytest.mark.asyncio` needed |
| `respx` + `respx_mock` fixture | Mock `httpx` HTTP calls |
| `pytest-mock` (`mocker` fixture) | All other mocking — replaces `unittest.mock` |
| `polyfactory` (`ModelFactory`) | Generate valid Pydantic v2 test data |
| `tmp_path` | Temporary file I/O — built-in pytest fixture |
| `monkeypatch` | Patch settings/module attributes — built-in pytest fixture |
| In-memory SQLite | DB-touching tests via `create_async_engine("sqlite+aiosqlite:///:memory:")` |

---

## File Structure

Always organize in this order — constants, fixtures, tests:

```python
"""Module docstring."""

# 1. Constants / fixtures (module-level data)
PDF_URL = "https://..."
FAKE_PDF = b"%PDF-1.4 ..."

# 2. Fixtures — grouped together, NEVER buried between test functions
@pytest.fixture
def sample_entry() -> DocketEntry: ...

@pytest.fixture
def mock_openai(mocker) -> AsyncMock: ...

# 3. Tests
async def test_something(sample_entry, mock_openai): ...
```

---

## Mocking HTTP — `respx_mock`

Always use the `respx_mock` fixture. Never use the `@respx.mock` decorator.

```python
# ✅ Correct
async def test_fetch_feed_returns_entries(respx_mock):
    respx_mock.get(FEED_URL).mock(return_value=httpx.Response(200, text=RSS_FIXTURE))
    entries = await fetch_feed(FEED_URL)
    assert len(entries) == 2

# ❌ Wrong — decorator style
@respx.mock
async def test_fetch_feed_returns_entries():
    respx.get(FEED_URL).mock(...)
```

When multiple tests hit the same URL, extract a fixture factory:

```python
@pytest.fixture
def mock_feed(respx_mock):
    def _mock(*, text: str = RSS_FIXTURE, status_code: int = 200):
        return respx_mock.get(FEED_URL).mock(return_value=httpx.Response(status_code, text=text))
    return _mock

async def test_parses_entries(mock_feed):
    mock_feed()
    entries = await fetch_feed(FEED_URL)
    assert len(entries) == 2
```

---

## Mocking — `mocker` fixture

Always use `mocker.patch()`. Never use `unittest.mock.patch` context managers or import `AsyncMock`/`MagicMock` for patching.

```python
# ✅ Correct — mocker auto-detects async functions, returns AsyncMock automatically
async def test_sync_inserts_entries(saved_case, raw_entry, mocker):
    mocker.patch("docketmind.ingestion.pipeline.fetch_feed", return_value=[raw_entry])
    mocker.patch("docketmind.ingestion.pipeline.update_case_memory", return_value="summary")
    result = await sync_case("case-001")
    assert result.new_entries == 1

# ❌ Wrong — verbose context managers, manual AsyncMock
async def test_sync_inserts_entries(saved_case, raw_entry):
    with (
        patch("docketmind.ingestion.pipeline.fetch_feed", AsyncMock(return_value=[raw_entry])),
        patch("docketmind.ingestion.pipeline.update_case_memory", AsyncMock(return_value="summary")),
    ):
        result = await sync_case("case-001")
```

When multiple tests share the same set of patches, extract a `_mocks` fixture:

```python
@pytest.fixture
def pipeline_mocks(mocker):
    return {
        "fetch_feed": mocker.patch("docketmind.ingestion.pipeline.fetch_feed"),
        "get_index": mocker.patch("docketmind.ingestion.pipeline.get_index", return_value=MagicMock()),
        "upsert_entry": mocker.patch("docketmind.ingestion.pipeline.upsert_entry"),
        "update_case_memory": mocker.patch("docketmind.ingestion.pipeline.update_case_memory", return_value="summary"),
    }

async def test_inserts_new_entries(saved_case, raw_entry, pipeline_mocks):
    pipeline_mocks["fetch_feed"].return_value = [raw_entry]
    result = await sync_case("case-001")
    assert result.new_entries == 1
```

---

## Parametrize for Repeated Assertions

When multiple tests assert different fields of the same response, use `@pytest.mark.parametrize` with descriptive `ids`:

```python
@pytest.mark.parametrize(
    ("entry_index", "field_name", "expected"),
    [
        (0, "pdf_urls", ["https://storage.courtlistener.com/recap/doc.pdf"]),
        (1, "pdf_urls", []),
        (0, "content", "Court grants defendant's motion to dismiss."),
    ],
    ids=["first-entry-pdf", "second-entry-no-pdf", "html-stripped"],
)
async def test_fetch_feed_extracts_fields(mock_feed, entry_index, field_name, expected):
    mock_feed()
    entries = await fetch_feed(FEED_URL)
    assert getattr(entries[entry_index], field_name) == expected
```

---

## DB Tests — In-Memory SQLite

Use `create_async_engine("sqlite+aiosqlite:///:memory:")` and swap the module-level session:

```python
@pytest.fixture(autouse=True)
async def in_memory_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db_module.engine = engine
    db_module.async_session = async_sessionmaker(engine, expire_on_commit=False)
    yield
    await engine.dispose()
```

Use `autouse=True` when every test in the file needs a DB. Scope it to the fixture file when only some tests need it.

---

## Settings Patching

Use `monkeypatch` to redirect `settings` attributes — never mutate the settings object directly:

```python
@pytest.fixture
def tmp_index_path(tmp_path: Path, monkeypatch):
    import docketmind.config as cfg
    monkeypatch.setattr(cfg.settings, "data_dir", tmp_path)
    return tmp_path / "index"
```

---

## Anti-Patterns

| Anti-pattern | Correct alternative |
|---|---|
| `@respx.mock` decorator | `respx_mock` fixture |
| `with patch(...) as mock:` context managers | `mocker.patch(...)` |
| `from unittest.mock import AsyncMock, patch` | Use `mocker`; remove imports |
| `assert route.called` / `assert len(route.calls) == 1` | Delete — tests `respx`, not your code |
| Repeated mock setup in every test body | Shared fixture (factory or `_mocks` dict) |
| `sample_document` fixture between test functions | Move all fixtures before first test |
| `tmp_path / "file.pdf"` + `write_bytes(...)` in two tests | Extract a `pdf_path` fixture |
| `patches = {"fetch": AsyncMock(...)}` dict in test body | `pipeline_mocks` fixture |

---

## Optional Dependencies

Use `pytest.importorskip` at module level for packages that may not be installed:

```python
pytest.importorskip("llama_index.readers.file", reason="llama-index-readers-file not installed")
```

---

## Quick Checklist

Before marking a test file complete:

- [ ] All fixtures are above the first test function
- [ ] Module-level constants are at the very top
- [ ] No `@respx.mock` decorators — using `respx_mock` fixture
- [ ] No `unittest.mock.patch` context managers — using `mocker.patch()`
- [ ] No `assert route.called` assertions
- [ ] Repeated setup extracted into shared fixtures
- [ ] Parametrize used where 3+ tests differ only in inputs/outputs
- [ ] DB tests use in-memory SQLite fixture
