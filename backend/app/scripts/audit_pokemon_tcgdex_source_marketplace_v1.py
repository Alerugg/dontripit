from __future__ import annotations

import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import create_engine, text


EXPECTED_TCGDEX_SHA = os.environ.get(
    "TCGDEX_CARDS_SHA", "d9083b73db080979123ebf5e9e97338d4e0745b2"
).strip()
TCGDEX_REPO = Path(os.environ.get("TCGDEX_CARDS_REPO", "/tmp/tcgdex-cards"))
OUTPUT = Path(
    os.environ.get(
        "POKEMON_SOURCE_MARKETPLACE_OUTPUT",
        "artifacts/pokemon-source-marketplace-image-v1.json",
    )
)


def _git_head(repo: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()


def _skip_string(text_value: str, i: int) -> int:
    quote = text_value[i]
    i += 1
    while i < len(text_value):
        ch = text_value[i]
        if ch == "\\":
            i += 2
            continue
        i += 1
        if ch == quote:
            return i
    return i


def _skip_comment(text_value: str, i: int) -> int:
    if text_value.startswith("//", i):
        end = text_value.find("\n", i + 2)
        return len(text_value) if end < 0 else end + 1
    if text_value.startswith("/*", i):
        end = text_value.find("*/", i + 2)
        return len(text_value) if end < 0 else end + 2
    return i


def _extract_balanced(text_value: str, start: int) -> str | None:
    if start >= len(text_value) or text_value[start] not in "{[(":
        return None
    pairs = {"{": "}", "[": "]", "(": ")"}
    stack = [pairs[text_value[start]]]
    i = start + 1
    while i < len(text_value):
        ch = text_value[i]
        if ch in "'\"`":
            i = _skip_string(text_value, i)
            continue
        if text_value.startswith("//", i) or text_value.startswith("/*", i):
            i = _skip_comment(text_value, i)
            continue
        if ch in pairs:
            stack.append(pairs[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
            if not stack:
                return text_value[start : i + 1]
        i += 1
    return None


def _split_top_level(value: str) -> list[str]:
    body = value.strip()
    if len(body) >= 2 and body[0] in "{[" and body[-1] in "}]":
        body = body[1:-1]
    parts: list[str] = []
    start = 0
    stack: list[str] = []
    pairs = {"{": "}", "[": "]", "(": ")"}
    i = 0
    while i < len(body):
        ch = body[i]
        if ch in "'\"`":
            i = _skip_string(body, i)
            continue
        if body.startswith("//", i) or body.startswith("/*", i):
            i = _skip_comment(body, i)
            continue
        if ch in pairs:
            stack.append(pairs[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
        elif ch == "," and not stack:
            parts.append(body[start:i])
            start = i + 1
        i += 1
    tail = body[start:]
    if tail.strip():
        parts.append(tail)
    return parts


def _object_properties(value: str) -> dict[str, str]:
    props: dict[str, str] = {}
    for part in _split_top_level(value):
        match = re.match(r"\s*([A-Za-z_$][\w$]*)\s*:\s*(.*)\Z", part, re.S)
        if match:
            props[match.group(1)] = match.group(2).strip()
    return props


def _literal_string(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip()
    match = re.match(r"^(['\"])(.*?)\1$", raw, re.S)
    return match.group(2) if match else None


def _literal_bool(value: str | None) -> bool | None:
    raw = (value or "").strip().lower()
    if raw == "true":
        return True
    if raw == "false":
        return False
    return None


def _marketplace_ids(value: str | None) -> dict[str, int]:
    if not value:
        return {}
    out: dict[str, int] = {}
    for key in ("tcgplayer", "cardmarket"):
        match = re.search(rf"\b{key}\s*:\s*(\d+)\b", value)
        if match:
            out[key] = int(match.group(1))
    return out


def _card_root(source: str) -> str | None:
    match = re.search(r"\bconst\s+\w+\s*:\s*Card\s*=\s*{", source)
    if not match:
        return None
    start = source.find("{", match.start())
    return _extract_balanced(source, start)


def _variant_objects(value: str | None) -> list[dict[str, Any]]:
    if not value or not value.strip().startswith("["):
        return []
    variants: list[dict[str, Any]] = []
    for raw_item in _split_top_level(value):
        raw_item = raw_item.strip()
        if not raw_item.startswith("{"):
            continue
        props = _object_properties(raw_item)
        variants.append(
            {
                "type": _literal_string(props.get("type")),
                "subtype": _literal_string(props.get("subtype")),
                "stamp": _literal_string(props.get("stamp")),
                "foil": _literal_bool(props.get("foil")),
                "marketplace": _marketplace_ids(props.get("thirdParty")),
            }
        )
    return variants


def _build_set_index(data_root: Path) -> tuple[dict[str, Path], dict[str, list[str]]]:
    found: dict[str, list[Path]] = defaultdict(list)
    set_pattern = re.compile(r"\bconst\s+\w+\s*:\s*Set\s*=\s*{")
    id_pattern = re.compile(r"\bid\s*:\s*(['\"])(.*?)\1")
    for path in data_root.rglob("*.ts"):
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if not set_pattern.search(content):
            continue
        match = id_pattern.search(content)
        if match:
            found[match.group(2)].append(path)
    index: dict[str, Path] = {}
    duplicates: dict[str, list[str]] = {}
    for set_id, paths in found.items():
        if len(paths) == 1:
            index[set_id] = paths[0]
        else:
            duplicates[set_id] = [str(p) for p in paths]
    return index, duplicates


def _resolve_source_file(tcgdex_id: str, set_index: dict[str, Path]) -> tuple[str | None, str | None, Path | None]:
    matches = [set_id for set_id in set_index if tcgdex_id.startswith(set_id + "-")]
    if not matches:
        return None, None, None
    set_id = max(matches, key=len)
    local_id = tcgdex_id[len(set_id) + 1 :]
    set_file = set_index[set_id]
    card_dir = set_file.with_suffix("")
    card_file = card_dir / f"{local_id}.ts"
    return set_id, local_id, card_file if card_file.is_file() else None


def _source_identity(card_file: Path) -> dict[str, Any]:
    content = card_file.read_text(encoding="utf-8")
    root = _card_root(content)
    if root is None:
        return {"parse_status": "missing_card_root"}
    props = _object_properties(root)
    base_market = _marketplace_ids(props.get("thirdParty"))
    variants = _variant_objects(props.get("variants"))
    options: list[dict[str, Any]] = []
    if base_market:
        options.append(
            {
                "location": "base",
                "type": "default",
                "subtype": None,
                "stamp": None,
                "foil": False,
                "marketplace": base_market,
            }
        )
    for idx, variant in enumerate(variants):
        options.append({"location": f"variant:{idx}", **variant})
    tcgplayer_ids = sorted(
        {
            int(option["marketplace"]["tcgplayer"])
            for option in options
            if option.get("marketplace", {}).get("tcgplayer")
        }
    )
    cardmarket_ids = sorted(
        {
            int(option["marketplace"]["cardmarket"])
            for option in options
            if option.get("marketplace", {}).get("cardmarket")
        }
    )
    return {
        "parse_status": "ok",
        "base_marketplace": base_market,
        "variants": variants,
        "options": options,
        "tcgplayer_ids": tcgplayer_ids,
        "cardmarket_ids": cardmarket_ids,
    }


def _canonical_default_nonfoil(row: dict[str, Any]) -> bool:
    return (not bool(row.get("is_foil"))) and str(row.get("variant") or "default").strip().lower() in {
        "",
        "default",
        "normal",
    }


def _strict_candidate(row: dict[str, Any], source: dict[str, Any]) -> tuple[bool, str]:
    if source.get("parse_status") != "ok":
        return False, "source_parse_failed"
    tcgplayer_ids = source.get("tcgplayer_ids") or []
    if len(tcgplayer_ids) != 1:
        return False, "tcgplayer_product_not_unique_within_source_card"
    options = [
        option
        for option in source.get("options") or []
        if option.get("marketplace", {}).get("tcgplayer") == tcgplayer_ids[0]
    ]
    if len(options) != 1:
        return False, "tcgplayer_product_attached_to_multiple_source_options"
    option = options[0]
    if option.get("location") == "base":
        if not _canonical_default_nonfoil(row):
            return False, "canonical_print_not_default_nonfoil_for_base_product"
        return True, "base_product_matches_default_nonfoil"

    source_type = str(option.get("type") or "").strip().lower()
    source_foil = option.get("foil")
    canonical_variant = str(row.get("variant") or "").strip().lower()
    canonical_foil = bool(row.get("is_foil"))
    # Fail closed for source variants unless the canonical row explicitly names the same
    # variant type and foil semantics agree. Legacy/default rows are never promoted.
    type_agrees = bool(source_type and canonical_variant == source_type)
    if source_foil is None:
        foil_agrees = canonical_foil == ("holo" in source_type or "foil" in source_type)
    else:
        foil_agrees = canonical_foil == bool(source_foil)
    if type_agrees and foil_agrees:
        return True, "explicit_source_variant_matches_canonical_variant"
    return False, "source_variant_not_proven_by_canonical_metadata"


def _probe_product(product_id: int) -> dict[str, Any]:
    url = f"https://tcgplayer-cdn.tcgplayer.com/product/{product_id}_in_1000x1000.jpg"
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Dontripit Pokemon source marketplace certifier/1.0", "Accept": "image/*"},
            timeout=20,
            stream=True,
        )
        prefix = next(response.iter_content(32), b"") if response.status_code == 200 else b""
        return {
            "url": url,
            "status": response.status_code,
            "content_type": response.headers.get("Content-Type"),
            "prefix_hex": prefix[:12].hex(),
        }
    except Exception as exc:  # pragma: no cover - network evidence
        return {"url": url, "status": type(exc).__name__, "error": str(exc)}


def main() -> int:
    if not TCGDEX_REPO.is_dir():
        raise RuntimeError(f"TCGdex source repository missing: {TCGDEX_REPO}")
    actual_sha = _git_head(TCGDEX_REPO)
    if actual_sha != EXPECTED_TCGDEX_SHA:
        raise RuntimeError(f"TCGdex source SHA mismatch: expected={EXPECTED_TCGDEX_SHA} actual={actual_sha}")

    data_root = TCGDEX_REPO / "data"
    set_index, duplicate_set_ids = _build_set_index(data_root)
    if duplicate_set_ids:
        raise RuntimeError(f"Duplicate TCGdex source set ids: {duplicate_set_ids}")

    engine = create_engine(os.environ["DATABASE_URL_UNPOOLED"], pool_pre_ping=True)
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        if conn.execute(text("SHOW transaction_read_only")).scalar_one() != "on":
            raise RuntimeError("Production audit connection is not read-only")
        rows = [
            dict(row)
            for row in conn.execute(
                text(
                    """
                    SELECT p.id AS print_id,p.tcgdex_id,p.collector_number,p.variant,
                           p.is_foil,p.rarity,p.print_key,c.name AS card_name,s.code AS set_code
                    FROM prints p
                    JOIN cards c ON c.id=p.card_id
                    JOIN games g ON g.id=c.game_id
                    LEFT JOIN sets s ON s.id=p.set_id
                    WHERE g.slug='pokemon' AND p.language='en'
                      AND p.tcgdex_id IS NOT NULL
                      AND NOT EXISTS (SELECT 1 FROM print_images pi WHERE pi.print_id=p.id)
                    ORDER BY p.id
                    """
                )
            ).mappings().all()
        ]
        conn.rollback()

    if len(rows) != 1558:
        raise RuntimeError(f"Expected 1558 exact EN missing Prints, found {len(rows)}")

    audited: list[dict[str, Any]] = []
    for row in rows:
        tcgdex_id = str(row["tcgdex_id"])
        set_id, local_id, card_file = _resolve_source_file(tcgdex_id, set_index)
        item: dict[str, Any] = dict(row)
        item.update(
            {
                "source_set_id": set_id,
                "source_local_id": local_id,
                "source_file": str(card_file.relative_to(TCGDEX_REPO)) if card_file else None,
            }
        )
        if card_file is None:
            item["source"] = {"parse_status": "source_file_not_found"}
        else:
            item["source"] = _source_identity(card_file)
        candidate, reason = _strict_candidate(item, item["source"])
        item["strict_candidate"] = candidate
        item["strict_candidate_reason"] = reason
        item["strict_tcgplayer_product_id"] = (
            (item["source"].get("tcgplayer_ids") or [None])[0] if candidate else None
        )
        audited.append(item)

    product_to_prints: Counter[int] = Counter(
        int(item["strict_tcgplayer_product_id"])
        for item in audited
        if item.get("strict_tcgplayer_product_id")
    )
    for item in audited:
        product_id = item.get("strict_tcgplayer_product_id")
        item["strict_product_global_1to1"] = bool(
            product_id and product_to_prints[int(product_id)] == 1
        )

    strict_1to1 = [
        item for item in audited if item["strict_candidate"] and item["strict_product_global_1to1"]
    ]
    unique_products = sorted({int(item["strict_tcgplayer_product_id"]) for item in strict_1to1})
    probes: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(_probe_product, product_id): product_id for product_id in unique_products}
        for future in as_completed(futures):
            probes[futures[future]] = future.result()
    for item in strict_1to1:
        item["probe"] = probes.get(int(item["strict_tcgplayer_product_id"]))

    verified = [
        item
        for item in strict_1to1
        if (item.get("probe") or {}).get("status") == 200
        and str((item.get("probe") or {}).get("content_type") or "").startswith("image/")
    ]

    source_found = [item for item in audited if item.get("source_file")]
    parsed = [item for item in source_found if item["source"].get("parse_status") == "ok"]
    reason_counts = Counter(item["strict_candidate_reason"] for item in audited)
    tcgplayer_count_distribution = Counter(
        str(len(item["source"].get("tcgplayer_ids") or [])) for item in parsed
    )
    option_count_distribution = Counter(
        str(len(item["source"].get("options") or [])) for item in parsed
    )
    canonical_variant_counts = Counter(
        f"foil={bool(item.get('is_foil'))}|variant={item.get('variant') or '<null>'}" for item in audited
    )

    summary = {
        "status": "pass",
        "production_writes": 0,
        "transaction_read_only": True,
        "tcgdex_source_sha": actual_sha,
        "tcgdex_set_index_count": len(set_index),
        "missing_en_exact": len(audited),
        "source_file_found": len(source_found),
        "source_file_missing": len(audited) - len(source_found),
        "source_file_parsed": len(parsed),
        "tcgplayer_id_count_distribution": dict(sorted(tcgplayer_count_distribution.items())),
        "source_option_count_distribution": dict(sorted(option_count_distribution.items())),
        "canonical_variant_counts": dict(canonical_variant_counts.most_common()),
        "strict_candidate_reason_counts": dict(reason_counts.most_common()),
        "strict_candidates": sum(1 for item in audited if item["strict_candidate"]),
        "strict_global_1to1": len(strict_1to1),
        "unique_strict_products": len(unique_products),
        "cdn_status_counts": dict(Counter(str(result.get("status")) for result in probes.values())),
        "verified_strict_images": len(verified),
    }
    report = {
        **summary,
        "verified_strict_samples": verified[:100],
        "strict_unverified_samples": [item for item in strict_1to1 if item not in verified][:100],
        "multiple_product_samples": [
            item for item in parsed if len(item["source"].get("tcgplayer_ids") or []) > 1
        ][:100],
        "single_product_noncandidate_samples": [
            item
            for item in parsed
            if len(item["source"].get("tcgplayer_ids") or []) == 1 and not item["strict_candidate"]
        ][:100],
        "source_missing_samples": [item for item in audited if not item.get("source_file")][:100],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
