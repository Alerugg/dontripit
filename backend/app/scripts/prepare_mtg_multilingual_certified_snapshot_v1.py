from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.ingest.connectors.scryfall_mtg_v2 import ScryfallMtgV2Connector
from app.mtg_identity_v2 import clean, finish_values
from app.scripts import certify_mtg_multilingual_ephemeral_v1 as certification

DEFAULT_MANIFEST = Path(__file__).with_name("mtg_multilingual_certified_manifest_v1.json")


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "dontripit-mtg-multilingual-certified-manifest-v1":
        raise RuntimeError("Unsupported MTG multilingual certified manifest")
    return value


def prepare(snapshot: Path, meta_path: Path, manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    connector = ScryfallMtgV2Connector()
    metadata = certification._all_cards_metadata(connector)
    bulk_type = clean(metadata.get("type")) or "all_cards"
    bulk_updated_at = str(metadata.get("updated_at") or "")
    if bulk_type != manifest["scryfall_bulk_type"]:
        raise AssertionError(f"Scryfall bulk type changed: {bulk_type}")
    if bulk_updated_at != manifest["scryfall_bulk_updated_at"]:
        raise AssertionError(
            "Certified Scryfall all_cards snapshot has moved; recertification required: "
            f"{bulk_updated_at} != {manifest['scryfall_bulk_updated_at']}"
        )
    url = connector._bulk_download_url(metadata)
    if not url:
        raise RuntimeError("Scryfall all_cards download URL missing")

    counts = Counter()
    stored_objects = 0
    with snapshot.open("w", encoding="utf-8", newline="\n") as handle:
        for card in certification._iter_all_cards(connector, url):
            counts["all_objects"] += 1
            if not certification._is_paper(card):
                continue
            counts["paper_objects"] += 1
            lang = clean(card.get("lang")).lower()
            if lang not in certification.LANGUAGES:
                continue
            counts[f"paper_{lang}_objects"] += 1
            counts[f"paper_{lang}_prints"] += len(finish_values(card))
            handle.write(json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            stored_objects += 1

    digest = certification._sha256(snapshot)
    expected_counts = manifest["source_counts"]
    actual_counts = dict(counts)
    if actual_counts != expected_counts:
        raise AssertionError(f"Certified Scryfall source counts moved: {actual_counts} != {expected_counts}")
    if stored_objects != int(manifest["stored_source_objects"]):
        raise AssertionError(f"Certified stored object count moved: {stored_objects}")
    if digest != manifest["normalized_snapshot_sha256"]:
        raise AssertionError(
            "Certified normalized Scryfall snapshot digest changed; recertification required: "
            f"{digest} != {manifest['normalized_snapshot_sha256']}"
        )

    report = {
        "status": "pass",
        "mode": "certified-scryfall-source-replay",
        "bulk_type": bulk_type,
        "bulk_updated_at": bulk_updated_at,
        "snapshot_sha256": digest,
        "sealed_scope": manifest["sealed_scope"],
        "stored_objects": stored_objects,
        "counts": actual_counts,
        "certification_run_id": manifest["certification_run_id"],
        "certification_commit": manifest["certification_commit"],
    }
    meta_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay and verify the certified MTG ES/JA Scryfall snapshot")
    parser.add_argument("--snapshot", default="/tmp/mtg-multilingual-certified-scryfall.jsonl")
    parser.add_argument("--meta", default="/tmp/mtg-multilingual-certified-scryfall-meta.json")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    args = parser.parse_args()
    prepare(Path(args.snapshot), Path(args.meta), Path(args.manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
