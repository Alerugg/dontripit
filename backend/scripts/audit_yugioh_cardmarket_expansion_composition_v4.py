#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


def norm_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", text)


def _score(cm_names: set[str], official_names: set[str]) -> dict:
    overlap = len(cm_names & official_names)
    if not cm_names or not official_names or not overlap:
        return {
            "overlap": overlap,
            "cm_coverage": 0.0,
            "official_recall": 0.0,
            "f1": 0.0,
            "jaccard": 0.0,
        }
    cm_coverage = overlap / len(cm_names)
    official_recall = overlap / len(official_names)
    f1 = 2 * cm_coverage * official_recall / (cm_coverage + official_recall)
    jaccard = overlap / len(cm_names | official_names)
    return {
        "overlap": overlap,
        "cm_coverage": cm_coverage,
        "official_recall": official_recall,
        "f1": f1,
        "jaccard": jaccard,
    }


def _profile_accepts(row: dict, profile: dict) -> bool:
    if row.get("best_pid") is None:
        return False
    return (
        int(row["overlap"]) >= int(profile["min_overlap"])
        and float(row["cm_coverage"]) >= float(profile["min_cm_coverage"])
        and float(row["f1"]) >= float(profile["min_f1"])
        and float(row["margin_f1"]) >= float(profile["min_margin_f1"])
        and not bool(row["tied_best"])
    )


def _round_metrics(row: dict) -> dict:
    out = dict(row)
    for key in ("cm_coverage", "official_recall", "f1", "jaccard", "margin_f1"):
        if key in out and out[key] is not None:
            out[key] = round(float(out[key]), 8)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Read-only calibration of a YGO Cardmarket expansion -> Konami release bridge "
            "using expansion card-name composition. Existing exact mappings are used only "
            "as a gold label for evaluation; they are never used in the composition score."
        )
    )
    ap.add_argument("--min-linked", type=int, default=5)
    ap.add_argument("--min-informative", type=int, default=5)
    ap.add_argument("--min-support", type=int, default=5)
    ap.add_argument("--min-evidence-coverage", type=float, default=0.25)
    ap.add_argument("--min-purity", type=float, default=0.95)
    ap.add_argument("--min-margin", type=int, default=3)
    ap.add_argument("--sample-limit", type=int, default=100)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()

    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    conn = psycopg2.connect(url)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh'")
            game_id = int(cur.fetchone()["id"])

            cur.execute(
                """
                SELECT external_id, expansion_external_id, name, date_added
                FROM external_catalog_products
                WHERE source='cardmarket'
                  AND game_id=%s
                  AND product_group='single'
                  AND expansion_external_id IS NOT NULL
                  AND trim(expansion_external_id)<>''
                  AND name IS NOT NULL
                """,
                (game_id,),
            )
            products = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT pr.print_id,
                       cr.external_id AS konami_pid,
                       cr.name AS konami_release_name,
                       c.name AS card_name
                FROM print_releases pr
                JOIN catalog_releases cr ON cr.id=pr.release_id
                JOIN prints p ON p.id=pr.print_id
                JOIN cards c ON c.id=p.card_id
                WHERE cr.game_id=%s
                  AND cr.source='konami_neuron'
                  AND cr.external_id IS NOT NULL
                  AND trim(cr.external_id)<>''
                  AND c.name IS NOT NULL
                """,
                (game_id,),
            )
            official = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT e.expansion_external_id, l.print_id
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                WHERE e.source='cardmarket'
                  AND e.game_id=%s
                  AND e.product_group='single'
                  AND e.expansion_external_id IS NOT NULL
                  AND trim(e.expansion_external_id)<>''
                  AND l.confidence='exact'
                  AND l.link_status IN ('accepted','mapped')
                """,
                (game_id,),
            )
            exact_links = [dict(r) for r in cur.fetchall()]

        cm_names_by_expansion: dict[str, set[str]] = defaultdict(set)
        cm_rows_by_expansion = Counter()
        cm_dates_by_expansion: dict[str, list[str]] = defaultdict(list)
        for row in products:
            expansion = str(row.get("expansion_external_id") or "").strip()
            name = norm_text(row.get("name"))
            if not expansion or not name:
                continue
            cm_names_by_expansion[expansion].add(name)
            cm_rows_by_expansion[expansion] += 1
            if row.get("date_added") is not None:
                cm_dates_by_expansion[expansion].append(str(row["date_added"]))

        official_names_by_pid: dict[str, set[str]] = defaultdict(set)
        official_pid_name: dict[str, str] = {}
        print_to_pids: dict[int, set[str]] = defaultdict(set)
        name_to_pids: dict[str, set[str]] = defaultdict(set)
        for row in official:
            pid = str(row.get("konami_pid") or "").strip()
            name = norm_text(row.get("card_name"))
            if not pid or not name:
                continue
            official_names_by_pid[pid].add(name)
            official_pid_name[pid] = str(row.get("konami_release_name") or "")
            print_to_pids[int(row["print_id"])].add(pid)
            name_to_pids[name].add(pid)

        exact_prints_by_expansion: dict[str, set[int]] = defaultdict(set)
        for row in exact_links:
            expansion = str(row.get("expansion_external_id") or "").strip()
            if expansion:
                exact_prints_by_expansion[expansion].add(int(row["print_id"]))

        # Rebuild the V3 consensus gold labels. These labels are used only after
        # composition ranking, never as ranking input.
        gold_by_expansion: dict[str, str] = {}
        gold_meta: dict[str, dict] = {}
        rejected_gold = Counter()
        for expansion, linked_prints in sorted(exact_prints_by_expansion.items()):
            if len(linked_prints) < args.min_linked:
                rejected_gold["insufficient_linked_prints"] += 1
                continue
            informative = [print_id for print_id in linked_prints if print_to_pids.get(print_id)]
            if len(informative) < args.min_informative:
                rejected_gold["insufficient_informative_prints"] += 1
                continue
            coverage = len(informative) / len(linked_prints)
            if coverage < args.min_evidence_coverage:
                rejected_gold["insufficient_evidence_coverage"] += 1
                continue
            support = Counter()
            for print_id in informative:
                for pid in print_to_pids[print_id]:
                    support[pid] += 1
            ranked = support.most_common()
            if not ranked:
                rejected_gold["no_release_support"] += 1
                continue
            winner, winner_support = ranked[0]
            runner_support = ranked[1][1] if len(ranked) > 1 else 0
            if len(ranked) > 1 and ranked[1][1] == winner_support:
                rejected_gold["tied_release_support"] += 1
                continue
            purity = winner_support / len(informative)
            if winner_support < args.min_support:
                rejected_gold["insufficient_winner_support"] += 1
                continue
            if purity < args.min_purity:
                rejected_gold["insufficient_release_purity"] += 1
                continue
            if winner_support - runner_support < args.min_margin:
                rejected_gold["insufficient_runner_up_margin"] += 1
                continue
            gold_by_expansion[expansion] = winner
            gold_meta[expansion] = {
                "linked": len(linked_prints),
                "informative": len(informative),
                "support": winner_support,
                "purity": purity,
                "coverage": coverage,
            }

        predictions: list[dict] = []
        no_candidate = 0
        for expansion, cm_names in sorted(cm_names_by_expansion.items()):
            candidate_pids: set[str] = set()
            for name in cm_names:
                candidate_pids.update(name_to_pids.get(name, set()))
            if not candidate_pids:
                no_candidate += 1
                predictions.append(
                    {
                        "expansion": expansion,
                        "cm_unique_names": len(cm_names),
                        "cm_product_rows": int(cm_rows_by_expansion[expansion]),
                        "best_pid": None,
                        "gold_pid": gold_by_expansion.get(expansion),
                        "is_gold": expansion in gold_by_expansion,
                    }
                )
                continue

            scored = []
            for pid in candidate_pids:
                metrics = _score(cm_names, official_names_by_pid[pid])
                scored.append(
                    (
                        float(metrics["f1"]),
                        float(metrics["jaccard"]),
                        float(metrics["cm_coverage"]),
                        int(metrics["overlap"]),
                        -abs(len(cm_names) - len(official_names_by_pid[pid])),
                        pid,
                        metrics,
                    )
                )
            scored.sort(reverse=True)
            best = scored[0]
            runner = scored[1] if len(scored) > 1 else None
            best_metrics = best[-1]
            runner_f1 = float(runner[0]) if runner else 0.0
            tied_best = bool(
                runner
                and abs(float(best[0]) - float(runner[0])) < 1e-12
                and abs(float(best[1]) - float(runner[1])) < 1e-12
                and abs(float(best[2]) - float(runner[2])) < 1e-12
                and int(best[3]) == int(runner[3])
            )
            pid = str(best[-2])
            row = {
                "expansion": expansion,
                "cm_unique_names": len(cm_names),
                "cm_product_rows": int(cm_rows_by_expansion[expansion]),
                "best_pid": pid,
                "best_release_name": official_pid_name.get(pid, ""),
                "official_unique_names": len(official_names_by_pid[pid]),
                "overlap": int(best_metrics["overlap"]),
                "cm_coverage": float(best_metrics["cm_coverage"]),
                "official_recall": float(best_metrics["official_recall"]),
                "f1": float(best_metrics["f1"]),
                "jaccard": float(best_metrics["jaccard"]),
                "runner_pid": str(runner[-2]) if runner else None,
                "runner_release_name": official_pid_name.get(str(runner[-2]), "") if runner else None,
                "runner_f1": runner_f1,
                "margin_f1": float(best_metrics["f1"]) - runner_f1,
                "tied_best": tied_best,
                "gold_pid": gold_by_expansion.get(expansion),
                "is_gold": expansion in gold_by_expansion,
                "gold_correct": (pid == gold_by_expansion[expansion]) if expansion in gold_by_expansion else None,
                "min_date_added": min(cm_dates_by_expansion[expansion]) if cm_dates_by_expansion[expansion] else None,
                "max_date_added": max(cm_dates_by_expansion[expansion]) if cm_dates_by_expansion[expansion] else None,
            }
            predictions.append(row)

        gold_predictions = [r for r in predictions if r.get("is_gold") and r.get("best_pid") is not None]
        raw_gold_correct = sum(1 for r in gold_predictions if r.get("gold_correct") is True)
        raw_gold_wrong = sum(1 for r in gold_predictions if r.get("gold_correct") is False)

        # Calibration grid. The result is advisory only; a later V4 certification
        # must hard-code a chosen profile and rerun read-only before any write path.
        profiles = []
        for min_overlap, min_cm_cov, min_f1, min_margin_f1 in itertools.product(
            (3, 5, 8, 10, 15),
            (0.80, 0.90, 0.95, 0.98, 1.0),
            (0.60, 0.70, 0.80, 0.85, 0.90, 0.95),
            (0.0, 0.01, 0.02, 0.05, 0.10),
        ):
            profile = {
                "min_overlap": min_overlap,
                "min_cm_coverage": min_cm_cov,
                "min_f1": min_f1,
                "min_margin_f1": min_margin_f1,
            }
            accepted = [r for r in gold_predictions if _profile_accepts(r, profile)]
            wrong = sum(1 for r in accepted if r.get("gold_correct") is False)
            correct = len(accepted) - wrong
            profiles.append(
                {
                    **profile,
                    "gold_accepted": len(accepted),
                    "gold_correct": correct,
                    "gold_wrong": wrong,
                    "gold_precision": (correct / len(accepted)) if accepted else None,
                }
            )

        zero_wrong = [p for p in profiles if p["gold_accepted"] > 0 and p["gold_wrong"] == 0]
        zero_wrong.sort(
            key=lambda p: (
                p["gold_accepted"],
                p["min_overlap"],
                p["min_cm_coverage"],
                p["min_f1"],
                p["min_margin_f1"],
            ),
            reverse=True,
        )
        best_zero_wrong = zero_wrong[0] if zero_wrong else None

        accepted_new = []
        accepted_gold = []
        if best_zero_wrong:
            for row in predictions:
                if row.get("best_pid") is None or not _profile_accepts(row, best_zero_wrong):
                    continue
                if row.get("is_gold"):
                    accepted_gold.append(row)
                else:
                    accepted_new.append(row)

        wrong_samples = [
            _round_metrics(r)
            for r in gold_predictions
            if r.get("gold_correct") is False
        ][: args.sample_limit]

        payload = {
            "mode": "read_only_calibration",
            "game": "yugioh",
            "resolver": "cardmarket_konami_expansion_composition_v4",
            "summary": {
                "cardmarket_products": len(products),
                "cardmarket_expansions": len(cm_names_by_expansion),
                "official_release_rows": len(official),
                "official_releases": len(official_names_by_pid),
                "gold_certified_expansions": len(gold_by_expansion),
                "gold_predictions": len(gold_predictions),
                "raw_gold_correct": raw_gold_correct,
                "raw_gold_wrong": raw_gold_wrong,
                "raw_gold_top1_accuracy": round(raw_gold_correct / len(gold_predictions), 8) if gold_predictions else None,
                "expansions_without_any_name_candidate": no_candidate,
                "zero_wrong_profiles": len(zero_wrong),
                "best_zero_wrong_profile": best_zero_wrong,
                "best_profile_gold_accepted": len(accepted_gold),
                "best_profile_new_expansion_candidates": len(accepted_new),
                "calibration_ready": len(gold_by_expansion) >= 100 and best_zero_wrong is not None,
                "production_writes": 0,
            },
            "gold_rejected": dict(sorted(rejected_gold.items())),
            "best_zero_wrong_profile": best_zero_wrong,
            "top_zero_wrong_profiles": zero_wrong[:25],
            "wrong_top1_samples": wrong_samples,
            "best_profile_new_expansion_candidates": [
                _round_metrics(r) for r in accepted_new[: args.sample_limit]
            ],
            "best_profile_gold_samples": [
                _round_metrics(r) for r in accepted_gold[: args.sample_limit]
            ],
            "predictions": [_round_metrics(r) for r in predictions],
        }

        args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("YGO_EXPANSION_COMPOSITION_V4=" + json.dumps(payload["summary"], separators=(",", ":")))
        conn.rollback()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
