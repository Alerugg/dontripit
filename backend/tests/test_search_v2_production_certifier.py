import copy

import pytest
import requests

from app.scripts import audit_search_v2_production_latency_v1 as certifier


class _Response:
    def __init__(self, status_code=200, payload=None, latency_ms=100, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = {"x-app-response-time-ms": str(latency_ms), **(headers or {})}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self):
        return copy.deepcopy(self._payload)


class _HTTP:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def get(self, _url, timeout):
        assert timeout == certifier.REQUEST_TIMEOUT_SECONDS
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _payload(*, print_id=571, set_code="bs", collector_number="58", rarity="Common"):
    return {
        "pagination_mode": "canonical_name",
        "count": 1,
        "total": 1,
        "items": [
            {
                "card_id": 1,
                "card_key": "pokemon:base1-58",
                "name": "Pikachu",
                "game": "pokemon",
                "matched_print": {
                    "print_id": print_id,
                    "set_code": set_code,
                    "collector_number": collector_number,
                    "language": "en",
                    "rarity": rarity,
                    "exact_variant": "normal",
                    "variant_family": "normal",
                },
            }
        ],
    }


def test_certifier_reserves_retry_capacity_inside_public_request_window():
    planned = sum(max(1, int(case.get("samples", 1))) for case in certifier.CASES)
    assert planned == 20
    assert certifier.MAX_NETWORK_REQUESTS == 30
    assert certifier.MAX_NETWORK_REQUESTS - planned == 10


def test_transient_504_retries_and_records_recovery_without_hiding_it():
    http = _HTTP([_Response(504), _Response(200, _payload())])
    state = {"used": 0}

    response, attempts = certifier._request_with_retry(
        http,
        "https://example.test/api/v2/search",
        request_state=state,
        sleep_fn=lambda _seconds: None,
    )

    assert response.status_code == 200
    assert state["used"] == 2
    assert attempts == [
        {"attempt": 1, "status_code": 504, "transient": True},
        {"attempt": 2, "status_code": 200, "transient": False},
    ]


def test_transport_timeout_retries_but_nonretryable_400_does_not():
    timeout_http = _HTTP([requests.Timeout("read timeout"), _Response(200, _payload())])
    state = {"used": 0}
    response, attempts = certifier._request_with_retry(
        timeout_http,
        "https://example.test/api/v2/search",
        request_state=state,
        sleep_fn=lambda _seconds: None,
    )
    assert response.status_code == 200
    assert attempts[0]["transient"] is True
    assert attempts[0]["error"] == "Timeout"

    bad_http = _HTTP([_Response(400), _Response(200, _payload())])
    bad_state = {"used": 0}
    with pytest.raises(requests.HTTPError):
        certifier._request_with_retry(
            bad_http,
            "https://example.test/api/v2/search",
            request_state=bad_state,
            sleep_fn=lambda _seconds: None,
        )
    assert bad_state["used"] == 1
    assert bad_http.calls == 1


def test_semantic_fingerprint_changes_when_physical_identity_changes():
    original = _payload(print_id=571)
    same = copy.deepcopy(original)
    different = _payload(print_id=999)

    assert certifier._semantic_fingerprint(original) == certifier._semantic_fingerprint(same)
    assert certifier._semantic_fingerprint(original) != certifier._semantic_fingerprint(different)


def test_required_identity_placeholder_fails_closed_but_optional_null_is_allowed():
    payload = _payload()
    payload["items"][0]["matched_print"]["variant_family"] = None
    certifier._validate_payload(
        {"game": "pokemon", "query": "Pikachu", "kind": "core", "top_contains": "pikachu"},
        payload,
    )

    broken = _payload(set_code="unknown")
    with pytest.raises(AssertionError, match="missing/placeholder"):
        certifier._validate_payload(
            {"game": "pokemon", "query": "Pikachu", "kind": "core", "top_contains": "pikachu"},
            broken,
        )


def test_pikachu_certification_locks_known_physical_metadata():
    case = next(case for case in certifier.CASES if case["game"] == "pokemon" and case["query"] == "Pikachu")
    certifier._validate_payload(case, _payload())

    with pytest.raises(AssertionError, match="metadata regression"):
        certifier._validate_payload(case, _payload(print_id=999))


def test_repeated_successful_samples_must_have_one_semantic_fingerprint():
    case = {
        "game": "pokemon",
        "query": "Pikachu",
        "kind": "core",
        "top_contains": "pikachu",
        "samples": 2,
    }
    http = _HTTP(
        [
            _Response(200, _payload(print_id=571), latency_ms=100),
            _Response(200, _payload(print_id=999), latency_ms=110),
        ]
    )

    with pytest.raises(AssertionError, match="non-deterministic response fingerprint"):
        certifier._run_case(
            "https://example.test",
            case,
            http=http,
            request_state={"used": 0},
            sleep_fn=lambda _seconds: None,
        )
