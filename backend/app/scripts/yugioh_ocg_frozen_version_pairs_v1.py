from __future__ import annotations

import unicodedata
from collections import defaultdict

from app.scripts.yugioh_ocg_frozen_version_contract_v1 import EVIDENCE, contract_sha256

GAME = "yugioh"
ACCEPTED = ("accepted", "mapped", "exact")
EXPECTED_JA = 36426
EXPECTED_PAIRS = 41
TARGETS = {
    "DOCS": {"idExpansion": "4680", "products": 108, "prints": 108, "accepted": 69, "pairs": 19, "unsupported": 20},
    "LTGY": {"idExpansion": "4725", "products": 86, "prints": 86, "accepted": 75, "pairs": 11, "unsupported": 0},
    "CSOC": {"idExpansion": "4809", "products": 87, "prints": 87, "accepted": 74, "pairs": 11, "unsupported": 2},
}


def norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def rarity(value: object) -> str:
    raw = norm(value)
    aliases = {
        "superrare": "super",
        "super": "super",
        "ultrarare": "ultra",
        "ultra": "ultra",
        "secretrare": "secret",
        "secret": "secret",
        "ultimaterare": "ultimate",
        "ultimate": "ultimate",
        "holographicrare": "ghost",
        "ghostrare": "ghost",
        "ghost": "ghost",
        "commonparallelrare": "commonparallel",
        "parallelcommonrare": "commonparallel",
        "commonparallel": "commonparallel",
        "common": "common",
        "rare": "rare",
    }
    return aliases.get(raw, raw)


def rarity_key(rows: list[dict]) -> str:
    return "|".join(sorted(rarity(row.get("rarity")) for row in rows))


def derive(cur) -> dict:
    cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
    row = cur.fetchone()
    if not row:
        raise RuntimeError("Yu-Gi-Oh game missing")
    gid = int(row["id"])

    cur.execute(
        "SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",
        (gid,),
    )
    capture = cur.fetchone()["capture"]
    if capture is None:
        raise RuntimeError("Cardmarket capture missing")

    cur.execute(
        """SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'""",
        (gid,),
    )
    ja = int(cur.fetchone()["n"])
    if ja != EXPECTED_JA:
        raise RuntimeError({"ja_baseline_drift": ja})

    cur.execute(
        """SELECT e.metacard_external_id,p.card_id,count(*) evidence_links
        FROM external_catalog_print_links l
        JOIN external_catalog_products e ON e.id=l.external_product_id
        JOIN prints p ON p.id=l.print_id
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
          AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s)
        GROUP BY e.metacard_external_id,p.card_id""",
        (gid, list(ACCEPTED)),
    )
    meta_cards: dict[str, set[int]] = defaultdict(set)
    evidence_links: dict[tuple[str, int], int] = defaultdict(int)
    for r in cur.fetchall():
        meta = str(r["metacard_external_id"])
        cid = int(r["card_id"])
        meta_cards[meta].add(cid)
        evidence_links[(meta, cid)] += int(r["evidence_links"] or 0)

    cur.execute(
        """SELECT e.external_id id_product,l.external_product_id,l.print_id,l.mapping_method,l.confidence,l.reviewed
        FROM external_catalog_print_links l
        JOIN external_catalog_products e ON e.id=l.external_product_id
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
          AND l.link_status=ANY(%s)""",
        (gid, list(ACCEPTED)),
    )
    accepted = [dict(r) for r in cur.fetchall()]
    by_product: dict[str, list[dict]] = defaultdict(list)
    by_print: dict[int, list[dict]] = defaultdict(list)
    for r in accepted:
        by_product[str(r["id_product"])].append(r)
        by_print[int(r["print_id"])].append(r)

    all_pairs: list[dict] = []
    reports: list[dict] = []
    unsupported: list[dict] = []

    for code, cfg in TARGETS.items():
        evidence = EVIDENCE[code]
        if str(evidence["idExpansion"]) != cfg["idExpansion"]:
            raise RuntimeError({"contract_expansion_drift": code})
        contracts = {str(k): tuple(v) for k, v in evidence["contracts"].items()}

        cur.execute(
            """SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id
            FROM external_catalog_products e
            WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
              AND e.expansion_external_id=%s AND e.last_seen_at=%s
            ORDER BY e.metacard_external_id,e.external_id::bigint""",
            (gid, cfg["idExpansion"], capture),
        )
        products = [dict(r) for r in cur.fetchall()]

        cur.execute(
            """SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name
            FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
            WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s
              AND lower(coalesce(p.language,''))='ja'
            ORDER BY p.card_id,p.collector_number,p.id""",
            (gid, code),
        )
        prints = [dict(r) for r in cur.fetchall()]
        if (len(products), len(prints)) != (cfg["products"], cfg["prints"]):
            raise RuntimeError(
                {
                    "surface_drift": code,
                    "products": len(products),
                    "prints": len(prints),
                    "expected": cfg,
                }
            )

        pids = {str(x["id_product"]) for x in products}
        prids = {int(x["print_id"]) for x in prints}
        accepted_here = [
            r
            for r in accepted
            if str(r["id_product"]) in pids and int(r["print_id"]) in prids
        ]
        if len(accepted_here) != cfg["accepted"]:
            raise RuntimeError(
                {"accepted_surface_drift": code, "actual": len(accepted_here), "expected": cfg["accepted"]}
            )

        products_by_meta: dict[str, list[dict]] = defaultdict(list)
        prints_by_card: dict[int, list[dict]] = defaultdict(list)
        card_names: dict[int, str] = {}
        for x in products:
            products_by_meta[str(x.get("metacard_external_id") or "")].append(x)
        for x in prints:
            cid = int(x["card_id"])
            prints_by_card[cid].append(x)
            card_names[cid] = str(x["card_name"])

        set_pairs: list[dict] = []
        set_unsupported: list[dict] = []
        group_reports: list[dict] = []

        for meta, group in products_by_meta.items():
            if not meta or len(group) < 2:
                continue
            card_ids = meta_cards.get(meta, set())
            if len(card_ids) != 1:
                raise RuntimeError({"metacard_not_globally_unique": code, "idMetacard": meta, "card_ids": sorted(card_ids)})
            cid = next(iter(card_ids))
            cprints = prints_by_card.get(cid, [])
            if not cprints:
                raise RuntimeError({"resolved_card_missing_exact_set_ja_print": code, "idMetacard": meta, "card_id": cid})

            residual_products = [x for x in group if not by_product.get(str(x["id_product"]))]
            residual_prints = [x for x in cprints if not by_print.get(int(x["print_id"]))]
            if not residual_products and not residual_prints:
                continue
            if len(group) != len(cprints) or len(residual_products) != len(residual_prints):
                raise RuntimeError(
                    {
                        "residual_cardinality_drift": code,
                        "idMetacard": meta,
                        "products": len(group),
                        "prints": len(cprints),
                        "residual_products": len(residual_products),
                        "residual_prints": len(residual_prints),
                    }
                )

            key = rarity_key(cprints)
            sequence = contracts.get(key)
            base = {
                "set_code": code,
                "idExpansion": cfg["idExpansion"],
                "idMetacard": meta,
                "card_id": cid,
                "card_name": card_names[cid],
                "rarity_key": key,
                "group_size": len(group),
                "residual_products": len(residual_products),
                "residual_prints": len(residual_prints),
            }
            if sequence is None:
                item = {**base, "status": "unsupported_geometry"}
                set_unsupported.append(item)
                unsupported.append(item)
                group_reports.append(item)
                continue
            if len(sequence) != len(group):
                raise RuntimeError({"contract_group_size_drift": base, "sequence": sequence})

            print_by_rarity: dict[str, list[dict]] = defaultdict(list)
            for x in cprints:
                print_by_rarity[rarity(x.get("rarity"))].append(x)
            if set(print_by_rarity) != set(sequence):
                raise RuntimeError(
                    {
                        "contract_rarity_set_drift": base,
                        "contract": list(sequence),
                        "canonical": sorted(print_by_rarity),
                    }
                )
            if any(len(print_by_rarity[r]) != 1 for r in sequence):
                raise RuntimeError({"canonical_rarity_not_bijective": base})

            ordered_products = sorted(group, key=lambda x: int(x["id_product"]))
            pairs: list[dict] = []
            for ordinal, (prod, expected_rarity) in enumerate(zip(ordered_products, sequence), 1):
                pr = print_by_rarity[expected_rarity][0]
                pid = str(prod["id_product"])
                print_id = int(pr["print_id"])
                pclaims = by_product.get(pid, [])
                rclaims = by_print.get(print_id, [])
                if pclaims or rclaims:
                    same = any(int(r["print_id"]) == print_id for r in pclaims)
                    if not same or any(str(r["id_product"]) != pid for r in rclaims):
                        raise RuntimeError(
                            {
                                "accepted_identity_conflict": code,
                                "idProduct": pid,
                                "print_id": print_id,
                            }
                        )
                    continue
                pairs.append(
                    {
                        "set_code": code,
                        "idExpansion": cfg["idExpansion"],
                        "idMetacard": meta,
                        "external_product_id": int(prod["external_product_id"]),
                        "idProduct": pid,
                        "product_name": str(prod["name"]),
                        "product_ordinal": ordinal,
                        "contract_rarity": expected_rarity,
                        "print_id": print_id,
                        "card_id": cid,
                        "card_name": card_names[cid],
                        "collector_number": str(pr["collector_number"]),
                        "canonical_rarity": str(pr["rarity"]),
                        "canonical_variant": str(pr["variant"] or ""),
                        "contract_key": key,
                        "metacard_evidence_links": int(evidence_links.get((meta, cid), 0)),
                    }
                )

            if len(pairs) != len(residual_products):
                raise RuntimeError(
                    {
                        "candidate_pair_count_drift": code,
                        "idMetacard": meta,
                        "pairs": len(pairs),
                        "residual": len(residual_products),
                    }
                )
            group_reports.append({**base, "status": "certified_candidate", "candidate_pairs": len(pairs), "sequence": list(sequence)})
            set_pairs.extend(pairs)
            all_pairs.extend(pairs)

        if len(set_pairs) != cfg["pairs"] or sum(x["residual_products"] for x in set_unsupported) != cfg["unsupported"]:
            raise RuntimeError(
                {
                    "set_candidate_contract_drift": code,
                    "pairs": len(set_pairs),
                    "unsupported": sum(x["residual_products"] for x in set_unsupported),
                    "expected": cfg,
                }
            )
        reports.append(
            {
                "set_code": code,
                "idExpansion": cfg["idExpansion"],
                "products": len(products),
                "canonical_ja_prints": len(prints),
                "accepted_links": len(accepted_here),
                "residual_products": len(products) - len(accepted_here),
                "candidate_pairs": len(set_pairs),
                "unsupported_pairs": sum(x["residual_products"] for x in set_unsupported),
                "groups": group_reports,
            }
        )

    if len(all_pairs) != EXPECTED_PAIRS:
        raise RuntimeError({"global_candidate_count_drift": len(all_pairs)})
    if len({x["external_product_id"] for x in all_pairs}) != EXPECTED_PAIRS:
        raise RuntimeError("candidate external products are not unique")
    if len({x["idProduct"] for x in all_pairs}) != EXPECTED_PAIRS:
        raise RuntimeError("candidate idProducts are not unique")
    if len({x["print_id"] for x in all_pairs}) != EXPECTED_PAIRS:
        raise RuntimeError("candidate prints are not unique")
    if sum(x["residual_products"] for x in unsupported) != 22:
        raise RuntimeError({"unsupported_total_drift": sum(x["residual_products"] for x in unsupported)})

    return {
        "game_id": gid,
        "capture": capture,
        "ja_baseline": ja,
        "contract_sha256": contract_sha256(),
        "pairs": all_pairs,
        "sets": reports,
        "unsupported_groups": unsupported,
    }
