from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from sqlalchemy import func, select

from app.ingest.base import IngestStats, SourceConnector
from app.ingest.provenance import upsert_field_provenance
from app.ingest.normalization import trim_or_none
from app.models import Card, Game, Print, PrintIdentifier, PrintImage, Set, SourceRecord


class TcgdexPokemonConnector(SourceConnector):
    def _find_pokemon_game(self, session) -> Game | None:
        return session.execute(select(Game).where(Game.slug == "pokemon")).scalar_one_or_none()

    def _find_set(self, session, game_id: int, set_payload: dict) -> Set | None:
        set_code = (set_payload.get("code") or "").lower()
        set_tcgdex_id = (set_payload.get("tcgdex_id") or "").strip()

        if set_tcgdex_id:
            row = session.execute(select(Set).where(Set.game_id == game_id, Set.tcgdex_id == set_tcgdex_id)).scalar_one_or_none()
            if row is not None:
                return row
        if set_code:
            row = session.execute(select(Set).where(Set.game_id == game_id, Set.code == set_code)).scalar_one_or_none()
            if row is not None:
                return row

        set_name = (set_payload.get("name") or "").strip()
        if set_name:
            return session.execute(select(Set).where(Set.game_id == game_id, Set.name == set_name)).scalar_one_or_none()
        return None

    def _find_card(self, session, game_id: int, card_payload: dict) -> Card | None:
        card_name = (card_payload.get("name") or "").strip()
        tcgdex_card_id = (card_payload.get("id") or "").strip()
        if tcgdex_card_id:
            row = session.execute(select(Card).where(Card.game_id == game_id, Card.tcgdex_id == tcgdex_card_id)).scalar_one_or_none()
            if row is not None:
                return row
        if card_name:
            return session.execute(select(Card).where(Card.game_id == game_id, Card.name == card_name)).scalar_one_or_none()
        return None

    def _find_print(
        self,
        session,
        set_id: int,
        card_id: int,
        collector_number: str,
        tcgdex_print_id: str | None,
        language: str = "en",
        is_foil: bool = False,
        variant: str = "default",
    ) -> Print | None:
        if tcgdex_print_id:
            row = session.execute(select(Print).where(Print.tcgdex_id == tcgdex_print_id)).scalar_one_or_none()
            if row is not None:
                return row
        return session.execute(
            select(Print).where(
                Print.set_id == set_id,
                Print.card_id == card_id,
                Print.collector_number == collector_number,
                Print.language == language,
                Print.is_foil.is_(is_foil),
                Print.variant == variant,
            )
        ).scalar_one_or_none()

    def _can_backfill_tcgdex_ids(self, session, normalized_payload: dict) -> bool:
        game = self._find_pokemon_game(session)
        if game is None:
            return False

        set_payload = normalized_payload.get("set") or {}
        card_payload = normalized_payload.get("card") or {}
        set_row = self._find_set(session, game.id, set_payload)
        set_tcgdex_id = (set_payload.get("tcgdex_id") or "").strip()
        if set_row is not None and set_tcgdex_id and not (set_row.tcgdex_id or "").strip():
            return True

        card_row = self._find_card(session, game.id, card_payload)
        tcgdex_card_id = (card_payload.get("id") or "").strip()
        if card_row is not None and tcgdex_card_id and not (card_row.tcgdex_id or "").strip():
            return True

        collector_number = (card_payload.get("collector_number") or "").strip()
        if set_row is None or card_row is None or not collector_number:
            return False

        print_row = self._find_print(session, set_row.id, card_row.id, collector_number, tcgdex_card_id or None)
        if print_row is not None and tcgdex_card_id and not (print_row.tcgdex_id or "").strip():
            return True

        return False


    @staticmethod
    def _primary_image_url_from_base(image_base: object) -> str | None:
        base = trim_or_none(image_base)
        if not base:
            return None
        return f"{base}/high.webp"

    def _as_str(self, v):
        """Coerce API values to string safely."""
        if v is None:
            return ""
        if isinstance(v, str):
            return v
        if isinstance(v, dict):
            return str(
                v.get("official")
                or v.get("abbreviation")
                or v.get("id")
                or v.get("code")
                or v.get("name")
                or ""
            )
        return str(v)

    name = "tcgdex_pokemon"
    base_url_template = "https://api.tcgdex.net/v2/{lang}"

    def should_bootstrap(self, session, source, **kwargs) -> bool:
        incremental = bool(kwargs.get("incremental", True))
        if not incremental:
            return False

        pokemon_game_id = session.execute(select(Game.id).where(Game.slug == "pokemon")).scalar_one_or_none()
        if pokemon_game_id is None:
            return True

        pokemon_cards = session.execute(select(Card).where(Card.game_id == pokemon_game_id)).scalars().all()
        if not pokemon_cards:
            return True

        has_real_tcgdex_data = any((card.tcgdex_id or "").strip() for card in pokemon_cards)
        if not has_real_tcgdex_data:
            return True

        has_source_records = (
            session.execute(select(func.count()).select_from(SourceRecord).where(SourceRecord.source_id == source.id)).scalar_one() > 0
        )
        return not has_source_records

    def should_skip_existing_record(self, existing_record: SourceRecord, **kwargs) -> bool:
        payload = existing_record.raw_json or {}
        card_id = (payload.get("id") or "").strip()

        session = kwargs.get("session")
        if session is None:
            return True

        normalized = self.normalize(payload, **kwargs)
        if self._can_backfill_tcgdex_ids(session, normalized):
            self.logger.info("ingest tcgdex checksum hit; reason=backfill_possible card_id=%s", card_id or "<missing>")
            return False

        if not card_id:
            self.logger.info("ingest tcgdex skip reason=existing_by_checksum_no_card_id")
            return True

        game = self._find_pokemon_game(session)
        if game is None:
            return False

        already_linked = (
            session.execute(select(func.count(Card.id)).where(Card.game_id == game.id, Card.tcgdex_id == card_id)).scalar_one() > 0
        )
        if already_linked:
            self.logger.info("ingest skip connector=%s reason=existing_by_id tcgdex_card_id=%s", self.name, card_id)
            return True
        self.logger.info("ingest tcgdex checksum hit; reason=existing_by_checksum_but_not_linked card_id=%s", card_id)
        return False

    def load(self, path: str | Path | None = None, **kwargs) -> list[tuple[Path, dict, str]]:
        fixture = bool(kwargs.get("fixture", False))
        limit = kwargs.get("limit")
        set_id = kwargs.get("set")
        lang = kwargs.get("lang", "en")

        if fixture:
            fixture_path = self._resolve_fixture_path(path)
            cards = self._load_fixture(fixture_path, limit=limit)
        else:
            cards = self._load_remote(limit=limit, set_id=set_id, lang=lang)

        payloads: list[tuple[Path, dict, str]] = []
        for idx, card in enumerate(cards):
            payloads.append((Path(f"tcgdex_card_{card.get('id', idx)}.json"), card, self.checksum(card)))
        return payloads

    def _resolve_fixture_path(self, path: str | Path | None) -> Path:
        fixture_name = "tcgdex_pokemon_sample.json"
        backend_root = Path(__file__).resolve().parents[3]
        repo_root = backend_root.parent
        data_fixtures_relative = Path("data") / "fixtures" / fixture_name
        data_relative = Path("data") / fixture_name
        backend_data_fixtures_relative = Path("backend") / "data" / "fixtures" / fixture_name
        backend_data_relative = Path("backend") / "data" / fixture_name

        default_candidates = [
            backend_root / data_fixtures_relative,
            backend_root / backend_data_fixtures_relative,
            backend_root / data_relative,
            backend_root / backend_data_relative,
            repo_root / data_fixtures_relative,
            repo_root / backend_data_fixtures_relative,
            repo_root / data_relative,
            repo_root / backend_data_relative,
        ]

        attempted_paths: list[Path] = []
        seen: set[Path] = set()

        def _record(candidate: Path) -> None:
            if candidate in seen:
                return
            seen.add(candidate)
            attempted_paths.append(candidate)

        def _resolve_candidate(candidate: Path) -> Path | None:
            _record(candidate)
            if candidate.is_file():
                return candidate

            if candidate.is_dir():
                fixture_candidate = candidate / fixture_name
                _record(fixture_candidate)
                if fixture_candidate.is_file():
                    return fixture_candidate

                fixtures_child_candidate = candidate / "fixtures" / fixture_name
                _record(fixtures_child_candidate)
                if fixtures_child_candidate.is_file():
                    return fixtures_child_candidate

                if candidate.name == "fixtures":
                    sibling_candidate = candidate.parent / fixture_name
                    _record(sibling_candidate)
                    if sibling_candidate.is_file():
                        return sibling_candidate

            return None

        if path is None:
            for candidate in default_candidates:
                resolved = _resolve_candidate(candidate)
                if resolved is not None:
                    return resolved

            attempted_display = ", ".join(str(item) for item in attempted_paths)
            raise ValueError(
                "tcgdex_pokemon fixture file not found. "
                f"Attempted paths: {attempted_display}"
            )

        raw_path = Path(path)
        candidate_paths: list[Path]
        if raw_path.is_absolute():
            candidate_paths = [raw_path]
        else:
            candidate_paths = [raw_path, backend_root / raw_path, repo_root / raw_path]
            if raw_path.parts and raw_path.parts[0] == "backend":
                translated_path = Path(*raw_path.parts[1:])
                candidate_paths.extend(
                    [
                        translated_path,
                        backend_root / translated_path,
                        repo_root / translated_path,
                    ]
                )

        for candidate in candidate_paths:
            resolved = _resolve_candidate(candidate)
            if resolved is not None:
                return resolved

        attempted_display = ", ".join(str(item) for item in attempted_paths)
        raise ValueError(
            "Unable to resolve tcgdex_pokemon fixture path. "
            "Provide a valid fixture file or directory containing tcgdex_pokemon_sample.json. "
            f"Attempted paths: {attempted_display}"
        )

    def _load_fixture(self, fixture_path: Path, limit: int | None) -> list[dict]:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        cards = payload.get("cards") or []
        out: list[dict] = []
        for card in cards:
            out.append(card)
            if limit and len(out) >= limit:
                break
        return out

    def _build_card_payload(self, set_payload: dict, card_payload: dict) -> dict:
        return {
            "set": {
                "id": set_payload.get("id"),
                "abbreviation": set_payload.get("abbreviation"),
                "name": set_payload.get("name"),
                "releaseDate": set_payload.get("releaseDate"),
            },
            "id": card_payload.get("id"),
            "localId": card_payload.get("localId"),
            "name": card_payload.get("name"),
            "image": card_payload.get("image"),
        }

    def _load_remote(self, limit: int | None = None, set_id: str | None = None, lang: str = "en") -> list[dict]:
        base_url = self.base_url_template.format(lang=lang)
        out: list[dict] = []
        seen_card_ids: set[str] = set()

        if set_id:
            set_payload = self._request_json(f"{base_url}/sets/{set_id}")
            cards = set_payload.get("cards") or []
            for card in cards:
                card_id = card.get("id")
                if not card_id:
                    continue
                card_payload = self._request_json(f"{base_url}/cards/{card_id}")
                dedupe_id = str(card_payload.get("id") or card_id).strip()
                if dedupe_id and dedupe_id in seen_card_ids:
                    continue
                if dedupe_id:
                    seen_card_ids.add(dedupe_id)
                out.append(self._build_card_payload(set_payload, card_payload))
                if limit and len(out) >= limit:
                    return out
            return out

        sets = self._request_json(f"{base_url}/sets")
        for item in sets:
            remote_set_id = item.get("id")
            if not remote_set_id:
                continue
            set_payload = self._request_json(f"{base_url}/sets/{remote_set_id}")
            cards = set_payload.get("cards") or []
            for card in cards:
                dedupe_id = str(card.get("id") or "").strip()
                if dedupe_id and dedupe_id in seen_card_ids:
                    continue
                if dedupe_id:
                    seen_card_ids.add(dedupe_id)
                out.append(self._build_card_payload(set_payload, card))
                if limit and len(out) >= limit:
                    return out
        return out

    def _request_json(self, url: str, params: dict | None = None):
        wait_seconds = 0.3
        for _ in range(6):
            response = requests.get(url, params=params, timeout=30)
            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep(wait_seconds)
                wait_seconds *= 2
                continue
            response.raise_for_status()
            time.sleep(0.1)
            return response.json()
        raise RuntimeError(f"TCGdex request failed after retries: {url}")

    def normalize(self, payload: dict, **kwargs) -> dict:
        set_payload = payload.get("set") or {}
        set_tcgdex_id = self._as_str(set_payload.get("id")).strip()
        set_code = self._as_str(set_payload.get("abbreviation") or set_payload.get("id")).strip().lower()
        set_name = self._as_str(set_payload.get("name") or set_payload.get("id")).strip()
        return {
            "set": {
                "tcgdex_id": set_tcgdex_id,
                "code": set_code,
                "name": set_name,
                "released_at": set_payload.get("releaseDate"),
            },
            "card": {
                "id": payload.get("id"),
                "name": payload.get("name"),
                "collector_number": payload.get("localId"),
                "image": payload.get("image"),
            },
        }

    def upsert(self, session, payload: dict, stats: IngestStats, **kwargs) -> dict:
        game = self._find_pokemon_game(session)
        if game is None:
            game = Game(slug="pokemon", name="Pokémon")
            session.add(game)
            session.flush()
            stats.records_inserted += 1

        set_payload = payload.get("set") or {}
        set_code = self._as_str(set_payload.get("code")).strip().lower()
        set_tcgdex_id = self._as_str(set_payload.get("tcgdex_id")).strip()
        if not set_code and not set_tcgdex_id:
            return {}

        release_date = date.fromisoformat(set_payload["released_at"]) if set_payload.get("released_at") else None
        set_row = None
        if set_tcgdex_id:
            set_row = session.execute(
                select(Set).where(Set.game_id == game.id, Set.tcgdex_id == set_tcgdex_id)
            ).scalar_one_or_none()
        if set_row is None and set_code:
            set_row = session.execute(select(Set).where(Set.game_id == game.id, Set.code == set_code)).scalar_one_or_none()

        if set_row is None:
            if set_tcgdex_id:
                set_row = Set(
                    game_id=game.id,
                    code=set_code,
                    tcgdex_id=set_tcgdex_id,
                    name=set_payload.get("name") or set_code.upper(),
                    release_date=release_date,
                )
                session.add(set_row)
                session.flush()
                stats.records_inserted += 1
            else:
                return {}
        else:
            changed = False
            backfilled_set_id = False
            if set_code and set_row.code != set_code:
                set_row.code = set_code
                changed = True
            new_name = set_payload.get("name")
            if new_name and set_row.name != new_name:
                set_row.name = new_name
                changed = True
            if release_date and set_row.release_date != release_date:
                set_row.release_date = release_date
                changed = True
            if set_tcgdex_id and set_row.tcgdex_id != set_tcgdex_id:
                backfilled_set_id = not (set_row.tcgdex_id or "").strip()
                set_row.tcgdex_id = set_tcgdex_id
                changed = True
            if changed:
                stats.records_updated += 1
                if backfilled_set_id:
                    self.logger.info("ingest backfill set tcgdex_id set_code=%s tcgdex_id=%s", set_row.code, set_tcgdex_id)
                else:
                    self.logger.info("ingest update set set_code=%s tcgdex_id=%s", set_row.code, set_row.tcgdex_id)
            else:
                self.logger.info("ingest skip set already has tcgdex_id set_code=%s tcgdex_id=%s", set_row.code, set_row.tcgdex_id)

        card_payload = payload.get("card") or {}
        card_name = (card_payload.get("name") or "").strip()
        tcgdex_card_id = card_payload.get("id")
        if not card_name:
            return {}

        card_row = self._find_card(session, game.id, card_payload)

        if card_row is None:
            card_row = Card(game_id=game.id, name=card_name, tcgdex_id=tcgdex_card_id)
            session.add(card_row)
            session.flush()
            stats.records_inserted += 1
        else:
            changed = False
            backfilled_card_id = False
            if card_row.name != card_name:
                card_row.name = card_name
                changed = True
            if tcgdex_card_id and card_row.tcgdex_id != tcgdex_card_id:
                backfilled_card_id = not (card_row.tcgdex_id or "").strip()
                card_row.tcgdex_id = tcgdex_card_id
                changed = True
            if changed:
                stats.records_updated += 1
                if backfilled_card_id:
                    self.logger.info("ingest backfill card tcgdex_id card_name=%s tcgdex_id=%s", card_row.name, tcgdex_card_id)
                else:
                    self.logger.info("ingest update card card_name=%s tcgdex_id=%s", card_row.name, card_row.tcgdex_id)
            else:
                self.logger.info("ingest skip card already has tcgdex_id card_name=%s tcgdex_id=%s", card_row.name, card_row.tcgdex_id)

        tcgdex_print_id = tcgdex_card_id
        collector_number = card_payload.get("collector_number") or ""
        print_row = self._find_print(
            session,
            set_row.id,
            card_row.id,
            collector_number,
            tcgdex_print_id,
            language="en",
            is_foil=False,
            variant="default",
        )

        if print_row is None:
            print_row = Print(
                card_id=card_row.id,
                set_id=set_row.id,
                collector_number=collector_number,
                language="en",
                rarity="unknown",
                is_foil=False,
                tcgdex_id=tcgdex_print_id,
                variant="default",
            )
            session.add(print_row)
            session.flush()
            stats.records_inserted += 1
        else:
            changed = False
            backfilled_print_id = False
            if print_row.language != "en":
                print_row.language = "en"
                changed = True
            if print_row.rarity != "unknown":
                print_row.rarity = "unknown"
                changed = True
            if print_row.variant != "default":
                print_row.variant = "default"
                changed = True
            if tcgdex_print_id and print_row.tcgdex_id != tcgdex_print_id:
                backfilled_print_id = not (print_row.tcgdex_id or "").strip()
                print_row.tcgdex_id = tcgdex_print_id
                changed = True
            if print_row.collector_number != collector_number:
                print_row.collector_number = collector_number
                changed = True
            if changed:
                stats.records_updated += 1
                if backfilled_print_id:
                    self.logger.info(
                        "ingest backfill print tcgdex_id print_id=%s collector_number=%s tcgdex_id=%s",
                        print_row.id,
                        collector_number,
                        tcgdex_print_id,
                    )
                else:
                    self.logger.info(
                        "ingest update print print_id=%s collector_number=%s tcgdex_id=%s",
                        print_row.id,
                        print_row.collector_number,
                        print_row.tcgdex_id,
                    )
            else:
                self.logger.info(
                    "ingest skip print already has tcgdex_id print_id=%s collector_number=%s tcgdex_id=%s",
                    print_row.id,
                    print_row.collector_number,
                    print_row.tcgdex_id,
                )

        if tcgdex_print_id:
            identifier = session.execute(
                select(PrintIdentifier).where(PrintIdentifier.print_id == print_row.id, PrintIdentifier.source == "tcgdex")
            ).scalar_one_or_none()
            if identifier is None:
                session.add(PrintIdentifier(print_id=print_row.id, source="tcgdex", external_id=tcgdex_print_id))
                stats.records_inserted += 1
            elif identifier.external_id != tcgdex_print_id:
                identifier.external_id = tcgdex_print_id
                stats.records_updated += 1

        image_url = self._primary_image_url_from_base(card_payload.get("image"))
        if image_url:
            primary_image = session.execute(
                select(PrintImage).where(PrintImage.print_id == print_row.id, PrintImage.is_primary.is_(True))
            ).scalar_one_or_none()
            if primary_image is None:
                session.add(PrintImage(print_id=print_row.id, url=image_url, is_primary=True, source="tcgdex"))
                stats.records_inserted += 1
            elif primary_image.url != image_url:
                primary_image.url = image_url
                primary_image.source = "tcgdex"
                stats.records_updated += 1

        upsert_field_provenance(
            session,
            "print",
            print_row.id,
            kwargs.get("source_name", self.name),
            {
                "collector_number": print_row.collector_number,
                "tcgdex_id": print_row.tcgdex_id,
                "card_tcgdex_id": card_row.tcgdex_id,
            },
        )
        return {}

    def default_cursor(self, **kwargs) -> dict:
        return {
            "incremental": kwargs.get("incremental", True),
            "last_synced_at": datetime.now(timezone.utc).isoformat(),
            "strategy": "upsert_checksum",
        }
