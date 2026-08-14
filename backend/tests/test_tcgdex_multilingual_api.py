from sqlalchemy import select

from app import db
from app.auth.service import hash_api_key
from app.ingest.base import IngestStats
from app.ingest.connectors.tcgdex_pokemon_multilingual import MultilingualTcgdexPokemonConnector
from app.models import ApiKey, ApiPlan, Card, Print


def _auth_headers(key: str = "multilingual-api-key") -> dict[str, str]:
    with db.SessionLocal() as session:
        plan = session.execute(
            select(ApiPlan).where(ApiPlan.name == "free")
        ).scalar_one_or_none()
        if plan is None:
            plan = ApiPlan(name="free", monthly_quota_requests=5000, burst_rpm=60)
            session.add(plan)
            session.flush()

        api_key = session.execute(
            select(ApiKey).where(ApiKey.prefix == key[:8])
        ).scalar_one_or_none()
        if api_key is None:
            session.add(
                ApiKey(
                    key_hash=hash_api_key(key),
                    prefix=key[:8],
                    plan_id=plan.id,
                    is_active=True,
                    scopes=["read:catalog"],
                )
            )
            session.commit()
    return {"X-API-Key": key}


def _raw_card(*, language: str, set_name: str, card_name: str) -> dict:
    return {
        "_language": language,
        "set": {
            "id": "sv-api",
            "abbreviation": "SVAPI",
            "name": set_name,
            "releaseDate": "2026-08-14",
        },
        "id": "sv-api-001",
        "localId": "001",
        "name": card_name,
        "image": f"https://assets.tcgdex.net/{language}/sv/sv-api/001",
        "hp": 120,
        "stage": "Basic",
        "types": ["Fire"],
        "abilities": [],
        "attacks": [],
        "rules": [],
    }


def _seed_multilingual_pokemon() -> tuple[int, dict[str, int]]:
    connector = MultilingualTcgdexPokemonConnector()
    records = [
        _raw_card(language="en", set_name="Scarlet & Violet API", card_name="Charizard"),
        _raw_card(language="ja", set_name="スカーレット＆バイオレット API", card_name="リザードン"),
    ]
    with db.SessionLocal() as session:
        for raw in records:
            stats = IngestStats()
            normalized = connector.normalize(raw, lang=raw["_language"])
            connector.upsert(
                session,
                normalized,
                stats,
                lang=raw["_language"],
                source_name="tcgdex_pokemon",
            )
        session.commit()

        card = session.execute(
            select(Card).where(Card.tcgdex_id == "sv-api-001")
        ).scalar_one()
        prints = session.execute(
            select(Print).where(Print.card_id == card.id)
        ).scalars().all()
        return card.id, {row.language: row.id for row in prints}


def test_print_list_exposes_localized_display_fields_without_replacing_canonical_names(client):
    card_id, print_ids = _seed_multilingual_pokemon()

    response = client.get(
        f"/api/v1/prints?game=pokemon&card_id={card_id}",
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    items = response.get_json()
    assert isinstance(items, list)
    by_language = {item["language"]: item for item in items}

    en = by_language["en"]
    ja = by_language["ja"]
    assert int(en["id"]) == print_ids["en"]
    assert int(ja["id"]) == print_ids["ja"]

    assert en["card_name"] == "Charizard"
    assert en["set_name"] == "Scarlet & Violet API"
    assert en["localized_card_name"] == "Charizard"
    assert en["localized_set_name"] == "Scarlet & Violet API"
    assert en["display_name"] == "Charizard"

    # Canonical identity remains English/current while display fields are localized.
    assert ja["card_name"] == "Charizard"
    assert ja["set_name"] == "Scarlet & Violet API"
    assert ja["localized_card_name"] == "リザードン"
    assert ja["localized_set_name"] == "スカーレット＆バイオレット API"
    assert ja["display_name"] == "リザードン"
    assert ja["display_set_name"] == "スカーレット＆バイオレット API"


def test_card_detail_and_print_resolve_are_additively_localized(client):
    card_id, print_ids = _seed_multilingual_pokemon()
    headers = _auth_headers("resolve-multilingual-key")

    detail_response = client.get(f"/api/v1/cards/{card_id}", headers=headers)
    assert detail_response.status_code == 200
    detail = detail_response.get_json()
    # Logical Card identity is intentionally canonical and remains unchanged.
    assert detail["name"] == "Charizard"
    ja_detail = next(item for item in detail["prints"] if item["language"] == "ja")
    assert ja_detail["set_name"] == "Scarlet & Violet API"
    assert ja_detail["localized_card_name"] == "リザードン"
    assert ja_detail["display_name"] == "リザードン"

    resolve_response = client.post(
        "/api/v1/prints/resolve",
        json={"print_id": str(print_ids["ja"])},
        headers=headers,
    )
    assert resolve_response.status_code == 200
    resolved = resolve_response.get_json()["prints"][0]
    assert resolved["found"] is True
    catalog = resolved["catalog"]
    assert catalog["card_name"] == "Charizard"
    assert catalog["set_name"] == "Scarlet & Violet API"
    assert catalog["localized_card_name"] == "リザードン"
    assert catalog["localized_set_name"] == "スカーレット＆バイオレット API"
    assert catalog["display_name"] == "リザードン"
