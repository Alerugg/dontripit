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


def test_image_format_accepts_real_binary_signatures_and_rejects_html():
    assert audit._image_format(b"\xff\xd8\xff\xe0fixture") == "jpeg"
    assert audit._image_format(b"\x89PNG\r\n\x1a\nfixture") == "png"
    assert audit._image_format(b"GIF89afixture") == "gif"
    assert audit._image_format(b"RIFF\x00\x00\x00\x00WEBPfixture") == "webp"
    assert audit._image_format(b"\x00\x00\x00\x18ftypaviffixture") == "avif"
    assert audit._image_format(b"<?xml version='1.0'?><Error/>") is None
    assert audit._image_format(b"<!doctype html><html></html>") is None


def test_find_anchor_candidates_prefers_exact_normalized_name():
    rows = (
        _row("1", "Roronoa Zoro (OP16-035)", "100", "1621"),
        _row("2", "Roronoa Zoro (OP16-035) (V.2)", "101", "1621"),
    )
    result = audit.find_anchor_candidates(rows, "Roronoa Zoro (OP16-035)")
    assert [row.product_id for row in result] == ["1"]


def test_candidates_are_deduplicated_per_expansion_to_reduce_throttling():
    rows = (
        _row("1", "Alpha", "777"),
        _row("2", "Alpha", "777"),
        _row("3", "Alpha", "888"),
    )
    result = audit.find_anchor_candidates(rows, "Alpha")
    assert [(row.product_id, row.expansion_id) for row in result] == [("1", "777"), ("3", "888")]


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
        "probe_image",
        lambda url: {"status": 200, "image": url.endswith("/1/1.jpg") or url.endswith("/2/2.jpg"), "image_format": "jpeg"},
    )
    monkeypatch.setattr(audit.time, "sleep", lambda _: None)
    report = audit.certify_anchor_set(anchor_set, rows)
    assert report["status"] == "certified"
    assert report["confirmed_anchor_count"] == 2
    assert report["confirmed_expansion_ids"] == ["777"]
    # It stops after reaching the independent-anchor threshold.
    assert len(report["anchors"]) == 2


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
    monkeypatch.setattr(audit, "probe_image", lambda url: {"status": 200, "image": True, "image_format": "jpeg"})
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
    monkeypatch.setattr(audit, "probe_image", lambda url: {"status": 403, "image": False, "image_format": None})
    monkeypatch.setattr(audit.time, "sleep", lambda _: None)
    report = audit.certify_anchor_set(anchor_set, rows)
    assert report["status"] == "inconclusive"
    assert "must never be treated" in report["reason"]
