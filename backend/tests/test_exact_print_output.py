from contextlib import contextmanager

from flask import Flask, jsonify

from app.routes import exact_print_output


class _Mappings:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def execute(self, _sql, _params):
        # Two physical prints of one logical card: only print 101 owns an image.
        return _Mappings([
            {"id": 101, "exact_image_url": "https://images.example/print-101.jpg"},
            {"id": 102, "exact_image_url": None},
        ])


@contextmanager
def _session_local():
    yield _Session()


def test_sibling_print_image_is_removed(monkeypatch):
    app = Flask(__name__)
    monkeypatch.setattr(exact_print_output.db, "SessionLocal", _session_local)

    with app.test_request_context("/api/v1/prints"):
        # Legacy SQL has incorrectly borrowed print 101's image for print 102.
        response = jsonify([
            {"id": 101, "primary_image_url": "https://images.example/print-101.jpg"},
            {"id": 102, "primary_image_url": "https://images.example/print-101.jpg"},
        ])
        guarded = exact_print_output.enforce_exact_print_image_response(response)
        payload = guarded.get_json()

    assert payload[0]["primary_image_url"] == "https://images.example/print-101.jpg"
    assert payload[1]["primary_image_url"] is None


def test_guard_fails_closed_when_identity_query_fails(monkeypatch):
    app = Flask(__name__)

    @contextmanager
    def broken_session_local():
        raise exact_print_output.SQLAlchemyError("database unavailable")
        yield

    monkeypatch.setattr(exact_print_output.db, "SessionLocal", broken_session_local)

    with app.test_request_context("/api/prints/102"):
        response = jsonify({
            "id": 102,
            "image_url": "https://images.example/sibling.jpg",
            "primary_image_url": "https://images.example/sibling.jpg",
        })
        guarded = exact_print_output.enforce_exact_print_image_response(response)
        payload = guarded.get_json()

    assert payload["image_url"] is None
    assert payload["primary_image_url"] is None


def test_rolling_deploy_before_localization_migration_keeps_exact_output(monkeypatch):
    """New code must remain usable while production is still on migration 32.

    During the intended rollout, Vercel can promote the multilingual backend a
    moment before migration 33 creates ``print_localizations``. Exact image
    ownership is still available from the legacy tables and must keep working;
    only additive localization helpers are allowed to disappear temporarily.
    """
    app = Flask(__name__)

    monkeypatch.setattr(
        exact_print_output,
        "_exact_images",
        lambda ids: {102: "https://images.example/print-102.jpg"},
    )

    def missing_localization_table(_ids):
        # _localized_identity itself normally catches SQLAlchemyError from the
        # missing table and returns {}. Returning {} here locks the public
        # response contract expected in that rolling-deploy window.
        return {}

    monkeypatch.setattr(exact_print_output, "_localized_identity", missing_localization_table)

    with app.test_request_context("/api/v1/prints/102"):
        response = jsonify({
            "id": 102,
            "image_url": "https://images.example/print-102.jpg",
            "primary_image_url": "https://images.example/print-102.jpg",
            "card_name": "Celebi",
            "set_name": "Sword & Shield",
        })
        guarded = exact_print_output.enforce_exact_print_image_response(response)
        payload = guarded.get_json()

    assert payload["image_url"] == "https://images.example/print-102.jpg"
    assert payload["primary_image_url"] == "https://images.example/print-102.jpg"
    assert payload["card_name"] == "Celebi"
    assert payload["set_name"] == "Sword & Shield"
    assert "localized_card_name" not in payload
    assert "localized_set_name" not in payload
    assert "display_name" not in payload
    assert "display_set_name" not in payload
