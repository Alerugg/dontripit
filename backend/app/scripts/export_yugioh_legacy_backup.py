from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db


def _json_default(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _write_jsonl(path: Path, rows: list[dict]) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    count = 0
    with path.open("wb") as handle:
        for row in rows:
            line = (json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=_json_default) + "\n").encode("utf-8")
            handle.write(line)
            digest.update(line)
            count += 1
    return {"rows": count, "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def run(*, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    db.init_engine()
    with db.SessionLocal() as session:
        game = session.execute(text("SELECT * FROM games WHERE slug='yugioh' LIMIT 1")).mappings().one_or_none()
        if game is None:
            raise AssertionError("Yu-Gi-Oh game row not found")
        game_id = int(game["id"])

        queries = {
            "sets.jsonl": "SELECT * FROM sets WHERE game_id=:game ORDER BY id",
            "cards.jsonl": "SELECT * FROM cards WHERE game_id=:game ORDER BY id",
            "prints.jsonl": """
                SELECT p.* FROM prints p
                JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=:game
                ORDER BY p.id
            """,
            "print_images.jsonl": """
                SELECT pi.* FROM print_images pi
                JOIN prints p ON p.id=pi.print_id
                JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=:game
                ORDER BY pi.id
            """,
            "search_documents.jsonl": "SELECT * FROM search_documents WHERE game_id=:game ORDER BY id",
        }

        manifests = {}
        for filename, sql in queries.items():
            rows = [dict(row) for row in session.execute(text(sql), {"game": game_id}).mappings().all()]
            manifests[filename] = _write_jsonl(output_dir / filename, rows)

        game_payload = dict(game)
        (output_dir / "game.json").write_text(
            json.dumps(game_payload, ensure_ascii=False, indent=2, default=_json_default) + "\n",
            encoding="utf-8",
        )
        session.rollback()

    expected = {
        "sets.jsonl": 5463,
        "cards.jsonl": 2010,
        "prints.jsonl": 6216,
        "print_images.jsonl": 6221,
        "search_documents.jsonl": 13689,
    }
    for filename, expected_rows in expected.items():
        actual = int(manifests[filename]["rows"])
        if actual != expected_rows:
            raise AssertionError(f"Legacy backup count moved: {filename}={actual} expected={expected_rows}")

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "mode": "read_only_yugioh_legacy_recovery_export",
        "game_id": game_id,
        "files": manifests,
        "expected_rows": expected,
        "database_writes": 0,
        "purpose": "forensic recovery evidence before YGO V2 transactional replacement",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run(output_dir=args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
