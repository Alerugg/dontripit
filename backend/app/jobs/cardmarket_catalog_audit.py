from __future__ import annotations

import csv
import gzip
import io
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select

from app.models import Card, Game, Print, PrintIdentifier, Set


CARDMARKET_SOURCE = "cardmarket"


@dataclass(frozen=True)
class ProductListRow:
    product_id: str
    name: str
    category_id: str
    category: str
    expansion_id: str
    date_added: str | None = None
    metacard_id: str | None = None


@dataclass(frozen=True)
class AuditDecision:
    product_id: str
    name: str
    category: str
    expansion_id: str
    game: str | None
    set_code: str | None
    status: str
    print_id: int | None = None
    card_id: int | None = None
    evidence: dict | None = None

    def as_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "name": self.name,
            "category": self.category,
            "expansion_id": self.expansion_id,
            "game": self.game,
            "set_code": self.set_code,
            "status": self.status,
            "print_id": self.print_id,
            "card_id": self.card_id,
            "evidence": self.evidence or {},
        }


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = text.replace("’", "'").replace("‘", "'")
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_collector(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKC", str(value or "")).casefold())


def split_product_name_hints(name: str) -> tuple[str, str | None]:
    """Split a terminal parenthetical collector hint from the Cardmarket name.

    Current Cardmarket Product List rows can encode a collector/card number in
    the product name, e.g. ``Roronoa Zoro (OP01-001)``. We only treat the final
    parenthetical as a collector hint when it contains a digit. Other legitimate
    card-name parentheses remain part of the name.
    """
    raw = str(name or "").strip()
    match = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", raw)
    if not match:
        return raw, None
    base, hint = match.group(1).strip(), match.group(2).strip()
    if not base or not hint or not re.search(r"\d", hint) or len(hint) > 64:
        return raw, None
    return base, hint


def _first(mapping: dict, *names: str) -> str:
    normalized = {str(key).strip().casefold(): value for key, value in mapping.items()}
    for name in names:
        value = normalized.get(name.casefold())
        if value is not None:
            return str(value).strip()
    return ""


def _parse_product(raw: dict) -> ProductListRow | None:
    product_id = _first(raw, "idProduct", "product_id")
    name = _first(raw, "Name", "name")
    if not product_id or not name:
        return None
    return ProductListRow(
        product_id=product_id,
        name=name,
        category_id=_first(raw, "Category ID", "idCategory", "category_id"),
        category=_first(raw, "Category", "category", "categoryName"),
        expansion_id=_first(raw, "Expansion ID", "idExpansion", "expansion_id"),
        date_added=_first(raw, "Date Added", "date_added", "dateAdded") or None,
        metacard_id=_first(raw, "idMetacard", "metacard_id") or None,
    )


def load_product_list_bytes(content: bytes) -> list[ProductListRow]:
    """Parse current Cardmarket Product List JSON plus legacy CSV/gzip exports.

    A row is retained as long as Cardmarket gives us an idProduct and a name.
    Optional identity hints such as expansion/category may be absent, especially
    for non-single products; absence must become an auditable status later, not
    an invisible parser drop.
    """
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)

    stripped = content.lstrip()
    rows: list[ProductListRow] = []
    if stripped.startswith((b"{", b"[")):
        payload = json.loads(content.decode("utf-8-sig"))
        if isinstance(payload, dict):
            raw_rows = payload.get("products") or payload.get("data") or []
        elif isinstance(payload, list):
            raw_rows = payload
        else:
            raise ValueError("Unsupported Cardmarket Product List JSON root")
        if not isinstance(raw_rows, list):
            raise ValueError("Cardmarket Product List JSON products must be a list")
        for raw in raw_rows:
            if isinstance(raw, dict):
                parsed = _parse_product(raw)
                if parsed:
                    rows.append(parsed)
        return rows

    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("Cardmarket Product List CSV has no header")
    for raw in reader:
        parsed = _parse_product(raw)
        if parsed:
            rows.append(parsed)
    return rows


def load_product_list_file(path: str | Path) -> list[ProductListRow]:
    return load_product_list_bytes(Path(path).read_bytes())


def infer_game_from_category(category: str) -> str | None:
    value = normalize_name(category)
    if value in {"pokemon single", "pokémon single"}:
        return "pokemon"
    if value in {"magic single", "magic: the gathering single"}:
        return "mtg"
    if value in {"yu-gi-oh! single", "yugioh single", "yu-gi-oh single"}:
        return "yugioh"
    if value in {"one piece card game single", "one piece single"}:
        return "onepiece"
    return None


def load_expansion_crosswalk(path: str | Path) -> dict[str, dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Expansion crosswalk must be a JSON object keyed by Cardmarket Expansion ID")
    result: dict[str, dict] = {}
    for raw_id, raw_value in payload.items():
        expansion_id = str(raw_id).strip()
        if isinstance(raw_value, str):
            result[expansion_id] = {"set_code": raw_value.strip()}
        elif isinstance(raw_value, dict):
            result[expansion_id] = {
                "game": str(raw_value.get("game") or "").strip().lower() or None,
                "set_code": str(raw_value.get("set_code") or "").strip(),
            }
    return result


def _collector_matches(candidate: str | None, hint: str | None) -> bool:
    if not hint:
        return True
    candidate_key = normalize_collector(candidate)
    hint_key = normalize_collector(hint)
    if not candidate_key or not hint_key:
        return False
    return candidate_key == hint_key or hint_key.endswith(candidate_key)


def audit_product_list(session, rows: list[ProductListRow], crosswalk: dict[str, dict], *, game_filter: str = "") -> tuple[dict, list[AuditDecision]]:
    """Return evidence-only mapping decisions. This function never writes identifiers.

    When rows come from an official per-game Cardmarket feed, ``game_filter`` is
    authoritative fallback provenance if the category label is absent or new.
    A contradictory category/crosswalk is never silently filtered: it becomes an
    explicit ``game_conflict`` decision.
    """
    game_filter = str(game_filter or "").strip().lower()

    game_rows = session.execute(select(Game.id, Game.slug)).all()
    game_ids = {str(slug): int(game_id) for game_id, slug in game_rows}

    set_cache: dict[tuple[str, str], tuple[int, str] | None] = {}
    print_cache: dict[int, list[dict]] = {}

    cm_rows = session.execute(
        select(PrintIdentifier.external_id, PrintIdentifier.print_id)
        .where(PrintIdentifier.source == CARDMARKET_SOURCE)
    ).all()
    external_to_prints: dict[str, set[int]] = {}
    print_to_externals: dict[int, set[str]] = {}
    for external_id, print_id in cm_rows:
        external_to_prints.setdefault(str(external_id), set()).add(int(print_id))
        print_to_externals.setdefault(int(print_id), set()).add(str(external_id))

    def resolve_set(game_slug: str, set_code: str):
        key = (game_slug, set_code.casefold())
        if key in set_cache:
            return set_cache[key]
        game_id = game_ids.get(game_slug)
        if game_id is None:
            set_cache[key] = None
            return None
        row = session.execute(
            select(Set.id, Set.code)
            .where(Set.game_id == game_id, func.lower(Set.code) == set_code.casefold())
            .limit(2)
        ).all()
        set_cache[key] = (int(row[0][0]), str(row[0][1])) if len(row) == 1 else None
        return set_cache[key]

    def prints_for_set(set_id: int) -> list[dict]:
        if set_id in print_cache:
            return print_cache[set_id]
        found = session.execute(
            select(
                Print.id,
                Print.card_id,
                Card.name,
                Print.collector_number,
                Print.language,
                Print.is_foil,
                Print.variant,
            )
            .join(Card, Card.id == Print.card_id)
            .where(Print.set_id == set_id)
        ).all()
        items = [
            {
                "print_id": int(print_id),
                "card_id": int(card_id),
                "name": str(name or ""),
                "name_key": normalize_name(name or ""),
                "collector_number": collector_number,
                "language": language,
                "is_foil": bool(is_foil),
                "variant": variant,
            }
            for print_id, card_id, name, collector_number, language, is_foil, variant in found
        ]
        print_cache[set_id] = items
        return items

    decisions: list[AuditDecision] = []
    duplicate_product_ids = Counter(row.product_id for row in rows)

    for product in rows:
        inferred_game = infer_game_from_category(product.category)
        mapping = crosswalk.get(product.expansion_id) if product.expansion_id else None
        crosswalk_game = str((mapping or {}).get("game") or "").strip().lower() or None
        mapped_game = crosswalk_game or inferred_game or (game_filter or None)
        set_code = str((mapping or {}).get("set_code") or "").strip() or None
        base_name, collector_hint = split_product_name_hints(product.name)

        base = dict(
            product_id=product.product_id,
            name=product.name,
            category=product.category,
            expansion_id=product.expansion_id,
            game=mapped_game,
            set_code=set_code,
        )
        product_evidence = {
            "base_name": base_name,
            "collector_hint": collector_hint,
            "metacard_id": product.metacard_id,
            "category_game": inferred_game,
            "crosswalk_game": crosswalk_game,
            "feed_game": game_filter or None,
        }

        if duplicate_product_ids[product.product_id] > 1:
            decisions.append(AuditDecision(**base, status="duplicate_product_id", evidence={**product_evidence, "rows": duplicate_product_ids[product.product_id]}))
            continue
        if game_filter and inferred_game and inferred_game != game_filter:
            decisions.append(AuditDecision(**base, status="game_conflict", evidence=product_evidence))
            continue
        if game_filter and crosswalk_game and crosswalk_game != game_filter:
            decisions.append(AuditDecision(**base, status="game_conflict", evidence=product_evidence))
            continue
        if crosswalk_game and inferred_game and crosswalk_game != inferred_game:
            decisions.append(AuditDecision(**base, status="game_conflict", evidence=product_evidence))
            continue
        if mapped_game is None:
            decisions.append(AuditDecision(**base, status="unsupported_category", evidence=product_evidence))
            continue
        if not mapping or not set_code:
            decisions.append(AuditDecision(**base, status="missing_expansion_crosswalk", evidence=product_evidence))
            continue

        resolved_set = resolve_set(mapped_game, set_code)
        if resolved_set is None:
            decisions.append(AuditDecision(**base, status="set_not_unique_or_missing", evidence=product_evidence))
            continue
        set_id, canonical_set_code = resolved_set
        resolved_base = {**base, "set_code": canonical_set_code}

        name_candidates = [item for item in prints_for_set(set_id) if item["name_key"] == normalize_name(base_name)]
        candidates = [item for item in name_candidates if _collector_matches(item["collector_number"], collector_hint)]
        distinct_print_ids = sorted({item["print_id"] for item in candidates})

        if not distinct_print_ids:
            status = "collector_no_match" if name_candidates and collector_hint else "name_no_match"
            decisions.append(AuditDecision(**resolved_base, status=status, evidence={**product_evidence, "name_candidate_count": len(name_candidates)}))
            continue
        if len(distinct_print_ids) > 1:
            decisions.append(AuditDecision(
                **resolved_base,
                status="physical_ambiguity",
                evidence={
                    **product_evidence,
                    "candidate_count": len(distinct_print_ids),
                    "candidates": candidates[:20],
                },
            ))
            continue

        candidate = candidates[0]
        print_id = candidate["print_id"]
        existing_for_product = external_to_prints.get(product.product_id, set())
        existing_for_print = print_to_externals.get(print_id, set())
        if existing_for_product and existing_for_product != {print_id}:
            decisions.append(AuditDecision(**resolved_base, status="external_id_conflict", print_id=print_id, card_id=candidate["card_id"], evidence={**product_evidence, "mapped_print_ids": sorted(existing_for_product)}))
            continue
        if existing_for_print and existing_for_print != {product.product_id}:
            decisions.append(AuditDecision(**resolved_base, status="print_identifier_conflict", print_id=print_id, card_id=candidate["card_id"], evidence={**product_evidence, "existing_external_ids": sorted(existing_for_print)}))
            continue
        status = "already_mapped" if existing_for_product == {print_id} else "exact_candidate_review_required"

        decisions.append(AuditDecision(
            **resolved_base,
            status=status,
            print_id=print_id,
            card_id=candidate["card_id"],
            evidence={
                **product_evidence,
                "name_match": "normalized_exact",
                "collector_match": bool(collector_hint),
                "collector_number": candidate["collector_number"],
                "language": candidate["language"],
                "is_foil": candidate["is_foil"],
                "variant": candidate["variant"],
            },
        ))

    counts = Counter(item.status for item in decisions)
    summary = {
        "total_input_rows": len(rows),
        "audited_rows": len(decisions),
        "exact_candidates_review_required": counts.get("exact_candidate_review_required", 0),
        "already_mapped": counts.get("already_mapped", 0),
        "physical_ambiguity": counts.get("physical_ambiguity", 0),
        "missing_expansion_crosswalk": counts.get("missing_expansion_crosswalk", 0),
        "collector_no_match": counts.get("collector_no_match", 0),
        "status_counts": dict(sorted(counts.items())),
        "write_mode": "disabled",
    }
    return summary, decisions
