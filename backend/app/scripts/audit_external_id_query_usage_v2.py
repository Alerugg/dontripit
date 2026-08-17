from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


TOKENS = {
    "sets.tcgdex_id": ("Set.tcgdex_id", ".tcgdex_id", "tcgdex_id"),
    "sets.yugioh_id": ("Set.yugioh_id", ".yugioh_id", "yugioh_id"),
    "sets.riftbound_id": ("Set.riftbound_id", ".riftbound_id", "riftbound_id"),
    "cards.oracle_id": ("Card.oracle_id", ".oracle_id", "oracle_id"),
    "cards.tcgdex_id": ("Card.tcgdex_id", ".tcgdex_id", "tcgdex_id"),
    "cards.yugoprodeck_id": ("Card.yugoprodeck_id", ".yugoprodeck_id", "yugoprodeck_id"),
    "cards.riftbound_id": ("Card.riftbound_id", ".riftbound_id", "riftbound_id"),
    "prints.scryfall_id": ("Print.scryfall_id", ".scryfall_id", "scryfall_id"),
    "prints.tcgdex_id": ("Print.tcgdex_id", ".tcgdex_id", "tcgdex_id"),
    "prints.yugioh_id": ("Print.yugioh_id", ".yugioh_id", "yugioh_id"),
    "prints.riftbound_id": ("Print.riftbound_id", ".riftbound_id", "riftbound_id"),
}

SKIP_DIRS = {".git", ".next", "node_modules", "__pycache__"}


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _category(path: Path) -> str:
    text = path.as_posix()
    if "/alembic/" in f"/{text}":
        return "migration"
    if "/tests/" in f"/{text}":
        return "test"
    if text.endswith("/models.py") or text.endswith("_models.py"):
        return "model"
    if "/scripts/" in f"/{text}":
        return "script"
    return "runtime"


def run(*, repo_root: Path, report_path: Path | None = None) -> dict:
    files = []
    for path in repo_root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        files.append(path)

    matches: dict[str, list[dict]] = defaultdict(list)
    for path in sorted(files):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(repo_root)
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            window = "\n".join(lines[max(0, idx - 3): min(len(lines), idx + 4)])
            for key, tokens in TOKENS.items():
                if not any(token in line for token in tokens):
                    continue
                matches[key].append({
                    "path": rel.as_posix(),
                    "line": idx + 1,
                    "category": _category(rel),
                    "text": stripped[:400],
                    "window_mentions_game_id": "game_id" in window or "Game.slug" in window,
                    "window_mentions_variant": "variant" in window,
                    "window_mentions_finish": "finish" in window or "is_foil" in window,
                    "window_mentions_source_id_alone": bool(re.search(r"where\([^\n]*(?:oracle_id|tcgdex_id|yugoprodeck_id|riftbound_id|scryfall_id)\s*==", window, re.I)),
                })

    summary = {}
    for key, rows in sorted(matches.items()):
        category_counts = CounterLike(row["category"] for row in rows)
        runtime_rows = [row for row in rows if row["category"] in {"runtime", "script"}]
        summary[key] = {
            "references": len(rows),
            "by_category": category_counts,
            "runtime_or_script_references": len(runtime_rows),
            "runtime_without_game_context": sum(not row["window_mentions_game_id"] for row in runtime_rows),
            "runtime_without_variant_context": sum(not row["window_mentions_variant"] for row in runtime_rows),
        }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "static_external_id_query_usage_audit",
        "status": "pass",
        "summary": summary,
        "references": dict(matches),
        "notes": [
            "Static evidence only; PostgreSQL idx_scan evidence must be considered separately.",
            "A reference without nearby game_id/variant context is a review target, not automatically a bug.",
        ],
        "database_writes": 0,
    }
    _write(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def CounterLike(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--report-path", type=Path, default=None)
    args = parser.parse_args()
    run(repo_root=args.repo_root.resolve(), report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
