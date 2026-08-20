from datetime import date
from pathlib import Path
from types import SimpleNamespace

from scripts.sync_regional_content_daily import (
    _dedupe_records,
    _material_state,
    _record_for_source,
    _same_material,
)


def _source(**overrides):
    values = {
        "key": "source_a",
        "game": "pokemon",
        "regions": ("us",),
        "locale": "en-US",
        "name": "Official Source",
        "url": "https://example.com/news",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _item(**overrides):
    values = {
        "kind": "news",
        "item_url": "https://example.com/news/item-1",
        "title": "Official announcement",
        "published_date": date(2026, 8, 20),
        "release_date": None,
        "source_context": "Official announcement context",
    }
    values.update(overrides)
    return values


def test_stable_payload_has_no_fetch_timestamp():
    record = _record_for_source(_source(), _item(), "us")

    assert record["raw_json"]["official"] is True
    assert record["raw_json"]["regions"] == ["us"]
    assert "fetched_at" not in record["raw_json"]


def test_material_state_preserves_known_dates_when_source_omits_them():
    record = _record_for_source(
        _source(),
        _item(published_date=None, release_date=None),
        "us",
    )
    current = {
        "published_date": date(2026, 8, 1),
        "release_date": date(2026, 9, 1),
    }

    target = _material_state(record, game_id=7, current=current)

    assert target["published_date"] == date(2026, 8, 1)
    assert target["release_date"] == date(2026, 9, 1)


def test_identical_material_state_is_a_true_noop():
    record = _record_for_source(_source(), _item(), "us")
    target = _material_state(record, game_id=7)
    current = {
        "id": 123,
        "game_id": 7,
        "locale": target["locale"],
        "kind": target["kind"],
        "source_name": target["source_name"],
        "source_url": target["source_url"],
        "title": target["title"],
        "published_date": target["published_date"],
        "release_date": target["release_date"],
        "raw_json": target["raw_json"],
    }

    assert _same_material(current, target) is True


def test_title_dedupe_is_deterministic_and_keeps_aliases():
    base = _record_for_source(_source(), _item(), "us")
    weaker = {
        **base,
        "source_key": "source_b",
        "item_url": "https://example.com/news/longer-item-url",
        "published_date": None,
        "release_date": None,
    }
    stronger = {
        **base,
        "source_key": "source_a",
        "item_url": "https://example.com/news/item-1",
        "kind": "release",
        "release_date": date(2026, 9, 1),
    }

    records, decisions = _dedupe_records([weaker, stronger])

    assert len(records) == 1
    assert records[0]["item_url"] == stronger["item_url"]
    assert records[0]["raw_json"]["deduplicated_alias_urls"] == [weaker["item_url"]]
    assert len(decisions) == 1


def test_workflow_gates_before_writes_and_certifies_second_pass():
    repo_root = Path(__file__).resolve().parents[2]
    workflow = (repo_root / ".github/workflows/sync-regional-tcg-content.yml").read_text(
        encoding="utf-8"
    )

    dry_run = workflow.index("--dry-run")
    migration = workflow.index("alembic upgrade head")
    apply = workflow.index("--apply")

    assert dry_run < migration < apply
    assert "--certify-two-pass" in workflow
    assert "--verify-db" in workflow
    assert "ingest_official_regional_content" not in workflow
    assert "--cleanup-deprecated" not in workflow
