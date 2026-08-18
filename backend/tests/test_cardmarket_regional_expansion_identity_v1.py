from __future__ import annotations

from app.jobs.cardmarket_catalog_audit import ProductListRow
from app.scripts import audit_cardmarket_regional_expansion_identity_v1 as audit


def _row(product_id: str, name: str, expansion_id: str, category_id: str = "5") -> ProductListRow:
    return ProductListRow(
        product_id=product_id,
        name=name,
        category_id=category_id,
        category="Single",
        expansion_id=expansion_id,
    )


def test_image_url_uses_cardmarket_category_expansion_code_and_product_id():
    row = _row("123", "Card Scanner", "999")
    assert audit.image_url(row, "AGOV-JP") == (
        "https://product-images.s3.cardmarket.com/5/AGOV-JP/123/123.jpg"
    )


def test_find_anchor_candidates_prefers_exact_normalized_name():
    rows = (
        _row("1", "Roronoa Zoro (OP16-035)", "100", "1621"),
        _row("2", "Roronoa Zoro (OP16-035) (V.2)", "101", "1621"),
    )
    result = audit.find_anchor_candidates(rows, "Roronoa Zoro (OP16-035)")
    assert [row.product_id for row in result] == ["1"]


def test_certification_requires_two_independent_anchors_same_expansion(monkeypatch):
    anchor_set = audit.RegionalAnchorSet(
        key="fixture",
        game_slug="yugioh",
        expansion_code="TEST-JP",
        region="ocg_japan",
        official_expansion_url="https://www.cardmarket.com/fixture",
        anchors=("Alpha", "Beta", "Gamma"),
        min_confirmations=2,
    )
    rows = (
        _row("1", "Alpha", "777"),
        _row("2", "Beta", "777"),
        _row("3", "Gamma", "888"),
    )

    monkeypatch.setattr(
        audit,
        "probe_jpeg",
        lambda url: {"status": 200, "jpeg": url.endswith("/1/1.jpg") or url.endswith("/2/2.jpg")},
    )
    monkeypatch.setattr(audit.time, "sleep", lambda _: None)
    report = audit.certify_anchor_set(anchor_set, rows)
    assert report["status"] == "certified"
    assert report["confirmed_anchor_count"] == 2
    assert report["confirmed_expansion_ids"] == ["777"]


def test_conflict_fails_if_same_candidate_code_resolves_multiple_expansion_ids(monkeypatch):
    anchor_set = audit.RegionalAnchorSet(
        key="fixture",
        game_slug="yugioh",
        expansion_code="TEST-JP",
        region="ocg_japan",
        official_expansion_url="https://www.cardmarket.com/fixture",
        anchors=("Alpha", "Beta"),
        min_confirmations=2,
    )
    rows = (_row("1", "Alpha", "777"), _row("2", "Beta", "888"))
    monkeypatch.setattr(audit, "probe_jpeg", lambda url: {"status": 200, "jpeg": True})
    monkeypatch.setattr(audit.time, "sleep", lambda _: None)
    report = audit.certify_anchor_set(anchor_set, rows)
    assert report["status"] == "conflict"
    assert report["confirmed_expansion_ids"] == ["777", "888"]


def test_403_is_inconclusive_never_negative_identity(monkeypatch):
    anchor_set = audit.RegionalAnchorSet(
        key="fixture",
        game_slug="onepiece",
        expansion_code="OP16-JP",
        region="asia_region_legal",
        official_expansion_url="https://www.cardmarket.com/fixture",
        anchors=("Alpha", "Beta"),
        min_confirmations=2,
    )
    rows = (_row("1", "Alpha", "777", "1621"), _row("2", "Beta", "777", "1621"))
    monkeypatch.setattr(audit, "probe_jpeg", lambda url: {"status": 403, "jpeg": False})
    monkeypatch.setattr(audit.time, "sleep", lambda _: None)
    report = audit.certify_anchor_set(anchor_set, rows)
    assert report["status"] == "inconclusive"
    assert "must never be treated" in report["reason"]
