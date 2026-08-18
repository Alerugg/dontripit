from sqlalchemy import select

from app import db
from app.auth.service import hash_api_key
from app.models import ApiKey, ApiPlan, Card, Game, Print, PrintImage, Set
from app.multilingual_models import PrintLocalization


def _auth_headers(key: str = "exact-v2-key") -> dict[str, str]:
    with db.SessionLocal() as session:
        plan = session.execute(select(ApiPlan).where(ApiPlan.name == "free")).scalar_one_or_none()
        if plan is None:
            plan = ApiPlan(name="free", monthly_quota_requests=5000, burst_rpm=60)
            session.add(plan)
            session.flush()
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


def _seed_ygo_localized_prints(*, target_has_image: bool = True) -> dict[str, int]:
    with db.SessionLocal() as session:
        game = Game(slug="yugioh", name="Yu-Gi-Oh!")
        session.add(game)
        session.flush()

        set_ja = Set(game_id=game.id, code="QCCU-JP", name="Quarter Century Chronicle JP")
        set_es = Set(game_id=game.id, code="QCCU-SP", name="Quarter Century Chronicle ES")
        session.add_all([set_ja, set_es])
        session.flush()

        card = Card(game_id=game.id, name="Dark Magician")
        session.add(card)
        session.flush()

        ja_print = Print(
            set_id=set_ja.id,
            card_id=card.id,
            collector_number="QCCU-JP001",
            language="ja",
            rarity="Ultra Rare",
            variant="default",
        )
        es_print = Print(
            set_id=set_es.id,
            card_id=card.id,
            collector_number="QCCU-SP001",
            language="es",
            rarity="Ultra Rare",
            variant="default",
        )
        session.add_all([ja_print, es_print])
        session.flush()

        if target_has_image:
            session.add(
                PrintImage(
                    print_id=ja_print.id,
                    url="https://images.example.test/ygo/dark-magician-ja.jpg",
                    is_primary=True,
                    source="ygojson",
                )
            )
        session.add(
            PrintImage(
                print_id=es_print.id,
                url="https://images.example.test/ygo/dark-magician-es.jpg",
                is_primary=True,
                source="ygojson",
            )
        )

        session.add_all(
            [
                PrintLocalization(
                    print_id=ja_print.id,
                    language="ja",
                    source="ygojson",
                    external_id="ja-print-1",
                    card_name="ブラック・マジシャン",
                    set_name="クォーター・センチュリー・クロニクル",
                    details_json={
                        "effect": "魔法使いとしては、攻撃力・守備力ともに最高クラス。",
                        "pendulum_effect": None,
                        "official": True,
                    },
                ),
                PrintLocalization(
                    print_id=es_print.id,
                    language="es",
                    source="ygojson",
                    external_id="es-print-1",
                    card_name="Mago Oscuro",
                    set_name="Crónica del Cuarto de Siglo",
                    details_json={
                        "effect": "El mago definitivo en términos de ataque y defensa.",
                        "pendulum_effect": None,
                        "official": True,
                    },
                ),
            ]
        )
        session.commit()
        return {"ja": ja_print.id, "es": es_print.id}


def test_exact_print_detail_v2_separates_physical_ja_from_display_es(client):
    ids = _seed_ygo_localized_prints()
    response = client.get(
        f"/api/v1/prints/{ids['ja']}?locale=es-ES",
        headers=_auth_headers("exact-es-key"),
    )
    assert response.status_code == 200, response.get_json()
    payload = response.get_json()

    assert payload["detail_contract_version"] == 2
    assert payload["language"] == "ja"
    assert payload["physical"]["print_language"] == "ja"
    assert payload["primary_image_url"] == "https://images.example.test/ygo/dark-magician-ja.jpg"
    assert payload["image"] == {
        "primary_image_url": "https://images.example.test/ygo/dark-magician-ja.jpg",
        "has_exact_image": True,
        "source": "ygojson",
    }

    assert payload["printed"]["language"] == "ja"
    assert payload["printed"]["name"] == "ブラック・マジシャン"
    assert payload["printed"]["scope"] == "exact_print"
    assert payload["printed"]["source_print_id"] == ids["ja"]

    assert payload["display"]["requested_locale"] == "es-ES"
    assert payload["display"]["requested_language"] == "es"
    assert payload["display"]["resolved_language"] == "es"
    assert payload["display"]["fallback"] is False
    assert payload["display"]["scope"] == "card_display"
    assert payload["display"]["source_print_id"] == ids["es"]
    assert payload["display"]["name"] == "Mago Oscuro"
    assert payload["display"]["effect"] == "El mago definitivo en términos de ataque y defensa."
    assert payload["display_name"] == "Mago Oscuro"


def test_exact_print_detail_v2_never_borrows_sibling_image(client):
    ids = _seed_ygo_localized_prints(target_has_image=False)
    response = client.get(
        f"/api/v1/prints/{ids['ja']}?locale=es-ES",
        headers=_auth_headers("exact-img-key"),
    )
    assert response.status_code == 200, response.get_json()
    payload = response.get_json()

    assert payload["primary_image_url"] is None
    assert payload["image_url"] is None
    assert payload["image"]["primary_image_url"] is None
    assert payload["image"]["has_exact_image"] is False
    assert payload["display"]["name"] == "Mago Oscuro"


def test_exact_print_detail_v2_falls_back_to_physical_text_without_claiming_requested_locale(client):
    ids = _seed_ygo_localized_prints()
    response = client.get(
        f"/api/v1/prints/{ids['ja']}?locale=it-IT",
        headers=_auth_headers("exact-it-key"),
    )
    assert response.status_code == 200, response.get_json()
    payload = response.get_json()

    assert payload["display"]["requested_language"] == "it"
    assert payload["display"]["resolved_language"] == "ja"
    assert payload["display"]["fallback"] is True
    assert payload["display"]["scope"] == "exact_print"
    assert payload["display"]["name"] == "ブラック・マジシャン"
    assert payload["display"]["effect"].startswith("魔法使い")