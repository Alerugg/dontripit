from sqlalchemy import select

from app import db
from app.auth.service import hash_api_key
from app.ingest.base import IngestStats
from app.ingest.connectors.tcgdex_pokemon_multilingual import MultilingualTcgdexPokemonConnector
from app.models import ApiKey, ApiPlan, Card, Print
from app.multilingual_models import CardIdentifier


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


def _raw_card(
    *,
    language: str,
    set_id: str,
    set_name: str,
    card_id: str,
    card_name: str,
    local_id: str,
) -> dict:
    return {
        "_language": language,
        "set": {
            "id": set_id,
            "abbreviation": set_id,
            "name": set_name,
            "releaseDate": "2026-08-14",
        },
        "id": card_id,
        "localId": local_id,
        "name": card_name,
        "image": f"https://assets.tcgdex.net/{language}/{set_id}/{local_id}",
        "hp": 120,
        "stage": "Basic",
        "types": ["Fire"],
        "abilities": [],
        "attacks": [],
        "rules": [],
    }


def _seed_multilingual_pokemon() -> dict[str, int]:
    connector = MultilingualTcgdexPokemonConnector()
    records = [
        _raw_card(
            language="en",
            set_id="neo4",
            set_name="Neo Destiny",
            card_id="neo4-100",
            card_name="Lucky Stadium",
            local_id="100",
        ),
        _raw_card(
            language="es",
            set_id="neo4",
            set_name="Neo Destiny ES",
            card_id="neo4-100",
            card_name="Estadio Afortunado",
            local_id="100",
        ),
        _raw_card(
            language="ja",
            set_id="neo4",
            set_name="闇、そして光へ...",
            card_id="neo4-100",
            card_name="ビルからのメール",
            local_id="100",
        ),
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

        english_card = session.execute(
            select(Card).where(Card.tcgdex_id == "neo4-100")
        ).scalar_one()
        japanese_identifier = session.execute(
            select(CardIdentifier).where(
                CardIdentifier.source == "tcgdex:ja",
                CardIdentifier.external_id == "neo4-100",
            )
        ).scalar_one()
        japanese_card = session.get(Card, japanese_identifier.card_id)
        assert japanese_card is not None

        english_prints = session.execute(
            select(Print).where(Print.card_id == english_card.id)
        ).scalars().all()
        japanese_print = session.execute(
            select(Print).where(Print.card_id == japanese_card.id)
        ).scalar_one()
        by_language = {row.language: row.id for row in english_prints}
        return {
            "en_card_id": english_card.id,
            "ja_card_id": japanese_card.id,
            "en_print_id": by_language["en"],
            "es_print_id": by_language["es"],
            "ja_print_id": japanese_print.id,
        }


def test_print_list_exposes_overlay_and_regional_display_fields(client):
    seeded = _seed_multilingual_pokemon()

    response = client.get(
        "/api/v1/prints?game=pokemon",
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    items = response.get_json()
    assert isinstance(items, list)
    by_id = {int(item["id"]): item for item in items}

    en = by_id[seeded["en_print_id"]]
    es = by_id[seeded["es_print_id"]]
    ja = by_id[seeded["ja_print_id"]]

    assert en["card_name"] == "Lucky Stadium"
    assert en["set_name"] == "Neo Destiny"
    assert en["localized_card_name"] == "Lucky Stadium"
    assert en["display_name"] == "Lucky Stadium"

    # ES is an overlay on the English logical card/set identity.
    assert es["card_name"] == "Lucky Stadium"
    assert es["set_name"] == "Neo Destiny"
    assert es["localized_card_name"] == "Estadio Afortunado"
    assert es["localized_set_name"] == "Neo Destiny ES"
    assert es["display_name"] == "Estadio Afortunado"

    # JA is a separate physical catalog, so its canonical row is Japanese too.
    assert ja["card_name"] == "ビルからのメール"
    assert ja["set_name"] == "闇、そして光へ..."
    assert ja["localized_card_name"] == "ビルからのメール"
    assert ja["localized_set_name"] == "闇、そして光へ..."
    assert ja["display_name"] == "ビルからのメール"


def test_card_detail_and_print_resolve_preserve_japanese_regional_identity(client):
    seeded = _seed_multilingual_pokemon()
    headers = _auth_headers("resolve-multilingual-key")

    en_detail_response = client.get(
        f"/api/v1/cards/{seeded['en_card_id']}", headers=headers
    )
    assert en_detail_response.status_code == 200
    en_detail = en_detail_response.get_json()
    assert en_detail["name"] == "Lucky Stadium"
    es_detail = next(
        item for item in en_detail["prints"] if item["language"] == "es"
    )
    assert es_detail["card_name"] == "Lucky Stadium"
    assert es_detail["localized_card_name"] == "Estadio Afortunado"
    assert es_detail["display_name"] == "Estadio Afortunado"

    ja_detail_response = client.get(
        f"/api/v1/cards/{seeded['ja_card_id']}", headers=headers
    )
    assert ja_detail_response.status_code == 200
    ja_detail = ja_detail_response.get_json()
    assert ja_detail["name"] == "ビルからのメール"
    ja_print = ja_detail["prints"][0]
    assert ja_print["language"] == "ja"
    assert ja_print["card_name"] == "ビルからのメール"
    assert ja_print["display_name"] == "ビルからのメール"

    resolve_response = client.post(
        "/api/v1/prints/resolve",
        json={"print_id": str(seeded["ja_print_id"])},
        headers=headers,
    )
    assert resolve_response.status_code == 200
    resolved = resolve_response.get_json()["prints"][0]
    assert resolved["found"] is True
    catalog = resolved["catalog"]
    assert catalog["card_name"] == "ビルからのメール"
    assert catalog["set_name"] == "闇、そして光へ..."
    assert catalog["localized_card_name"] == "ビルからのメール"
    assert catalog["display_name"] == "ビルからのメール"
