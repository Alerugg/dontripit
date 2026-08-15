from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

import psycopg2
from psycopg2.extras import Json

from app.scripts import audit_yugioh_current_physical_delta_v2 as delta
from app.scripts import certify_yugioh_multilingual_ephemeral_v1 as historical_cert
from app.scripts.audit_yugioh_multilingual_cross_source_reconciliation import norm_rarity
from app.scripts.audit_yugioh_multilingual_yaml_yugi_current import iter_cards, mapping, s
from app.scripts.audit_yugioh_ygojson_canonical_print_projection import canonical_print_id, variant_for_rarity
from app.scripts.seed_yugioh_multilingual_ephemeral_lean_v1 import BASELINE_PATH, validate_lean_invariants

EXPECTED = {'es': 3938, 'ja': 5350}
EXPECTED_RARITY_CONFLICTS = {'es': 397, 'ja': 2833}
EXPECTED_MISSING_CARDS = {'es': 32, 'ja': 52}
TARGETS = {'es': {'language': 'es', 'region': 'global'}, 'ja': {'language': 'ja', 'region': 'jp'}}
EXPECTED_HEAD = '20260815_36'
SOURCE = 'yaml-yugi'


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items() if str(k).casefold() != 'price'}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _has_price(value: Any) -> bool:
    if isinstance(value, dict):
        return any(str(k).casefold() == 'price' or _has_price(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_price(v) for v in value)
    return False


def _text_payload(card: Mapping[str, Any], language: str) -> Any:
    value = mapping(card.get('text')).get(language)
    if isinstance(value, Mapping):
        return _clean(dict(value))
    raw = s(value)
    return raw or None


def _source_print_id(language: str, logical: str, collector: str, rarity: str) -> str:
    digest = hashlib.sha256(f'{language}\0{logical}\0{collector}\0{rarity}'.encode()).hexdigest()[:32]
    return f'yaml-yugi:{language}:{collector}:{digest}'


def _release_external_id(language: str, family: str, name: str) -> str:
    digest = hashlib.sha256(f'{language}\0{family}\0{name}'.encode()).hexdigest()[:24]
    return f'{language}:{family}:{digest}'


def _raw_index(cards_path: Path) -> dict[str, dict[tuple[str, str, str], dict[str, Any]]]:
    out: dict[str, dict[tuple[str, str, str], dict[str, Any]]] = {target: {} for target in TARGETS}
    conflicts: list[dict[str, Any]] = []
    for card in iter_cards(cards_path):
        logical = delta.yaml_logical_identity(card)
        if not logical:
            continue
        names = mapping(card.get('name'))
        sets = mapping(card.get('sets'))
        for target, spec in TARGETS.items():
            rows = sets.get(target) or []
            if isinstance(rows, Mapping):
                rows = [rows]
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                collector = s(row.get('set_number')).upper()
                if not collector or any(ch in collector for ch in ('?', '*')):
                    continue
                rarities = row.get('rarities') or []
                if not isinstance(rarities, list):
                    rarities = [rarities]
                if not rarities:
                    rarities = [None]
                for raw_rarity in rarities:
                    rarity = norm_rarity(raw_rarity) or 'unknown'
                    key = (logical, collector, rarity)
                    payload = out[target].setdefault(key, {
                        'names': set(), 'texts': [], 'set_names': set(), 'rarity_raw': set(),
                    })
                    name = s(names.get(spec['language']))
                    if name:
                        payload['names'].add(name)
                    text_payload = _text_payload(card, spec['language'])
                    if text_payload is not None and text_payload not in payload['texts']:
                        payload['texts'].append(text_payload)
                    set_name = s(row.get('set_name'))
                    if set_name:
                        payload['set_names'].add(set_name)
                    if s(raw_rarity):
                        payload['rarity_raw'].add(s(raw_rarity))
    for target, groups in out.items():
        for key, payload in groups.items():
            if len(payload['names']) > 1 or len(payload['texts']) > 1:
                conflicts.append({'target': target, 'key': key, 'names': sorted(payload['names']), 'texts': payload['texts'][:3]})
    if conflicts:
        raise AssertionError(f'Localized source conflicts: {json.dumps(conflicts[:20], ensure_ascii=False, default=str)}')
    return out


def _install_delta_guards(target_url: str, game_id: int) -> None:
    conn = psycopg2.connect(target_url, connect_timeout=30, application_name='dontripit_ygo_delta_guard')
    conn.set_session(readonly=False, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE OR REPLACE FUNCTION ygo_cert_guard_localization() RETURNS trigger AS $$
                DECLARE print_lang text;
                BEGIN
                  IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'YGO delta certification forbids localization %', TG_OP; END IF;
                  SELECT lower(coalesce(language,'')) INTO print_lang FROM prints WHERE id=NEW.print_id;
                  IF print_lang NOT IN ('es','ja') OR lower(coalesce(NEW.language,'')) <> print_lang OR NEW.source <> 'yaml-yugi' THEN
                    RAISE EXCEPTION 'YGO delta localization scope violation';
                  END IF;
                  RETURN NEW;
                END; $$ LANGUAGE plpgsql;
            """)
            cur.execute(f"""
                CREATE OR REPLACE FUNCTION ygo_cert_guard_release() RETURNS trigger AS $$
                BEGIN
                  IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'YGO delta certification forbids release %', TG_OP; END IF;
                  IF NEW.game_id <> {int(game_id)} OR NEW.source <> 'yaml-yugi' THEN
                    RAISE EXCEPTION 'YGO delta release scope violation';
                  END IF;
                  RETURN NEW;
                END; $$ LANGUAGE plpgsql;
            """)
            cur.execute("""
                CREATE OR REPLACE FUNCTION ygo_cert_guard_print_release() RETURNS trigger AS $$
                DECLARE release_source text; DECLARE print_lang text;
                BEGIN
                  IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'YGO delta certification forbids print_release %', TG_OP; END IF;
                  SELECT source INTO release_source FROM catalog_releases WHERE id=NEW.release_id;
                  SELECT lower(coalesce(language,'')) INTO print_lang FROM prints WHERE id=NEW.print_id;
                  IF release_source <> 'yaml-yugi' OR print_lang NOT IN ('es','ja') THEN
                    RAISE EXCEPTION 'YGO delta PrintRelease scope violation';
                  END IF;
                  RETURN NEW;
                END; $$ LANGUAGE plpgsql;
            """)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def build_plan(root: Path, cards_path: Path, source_meta: dict[str, Any], target_url: str) -> dict[str, Any]:
    historical, historical_meta = delta.build_historical(root)
    current, yaml_stats, invalid_samples = delta.build_current_yaml(cards_path)
    db = delta._db_state(target_url, current)
    raw = _raw_index(cards_path)
    targets: dict[str, Any] = {}
    releases: dict[str, dict[str, Any]] = {}
    quarantined: dict[str, Any] = {}
    name_coverage = Counter()
    text_coverage = Counter()

    for target, spec in TARGETS.items():
        hist = historical[target]
        cur = current[target]
        hist_coarse: dict[tuple[str, str], set[str]] = defaultdict(set)
        yaml_coarse: dict[tuple[str, str], set[str]] = defaultdict(set)
        for logical, collector, rarity in hist:
            hist_coarse[(logical, collector)].add(rarity)
        for logical, collector, rarity in cur:
            yaml_coarse[(logical, collector)].add(rarity)

        materializable: list[dict[str, Any]] = []
        missing_cards: list[dict[str, Any]] = []
        rarity_conflicts: list[dict[str, Any]] = []
        resolution_modes = Counter()
        for key in sorted(set(cur) - set(hist)):
            logical, collector, rarity = key
            data = cur[key]
            coarse = (logical, collector)
            old_rarities = hist_coarse.get(coarse, set())
            new_rarities = yaml_coarse.get(coarse, set())
            classification = (
                'new_card_collector' if not old_rarities
                else 'additional_rarity_on_known_collector' if old_rarities & new_rarities
                else 'rarity_disjoint_conflict'
            )
            if classification == 'rarity_disjoint_conflict':
                rarity_conflicts.append({'logical_card': logical, 'collector': collector, 'rarity': rarity})
                continue
            card_id, mode, ambiguous = delta._resolve_yaml_card(data, db['db_cards'], db['konami_to_db_cards'])
            if ambiguous:
                raise AssertionError(f'Ambiguous exact Card resolution: {target} {key}')
            if card_id is None:
                missing_cards.append({'logical_card': logical, 'collector': collector, 'rarity': rarity})
                continue
            resolution_modes[mode] += 1
            source = raw[target].get(key, {})
            names = sorted(source.get('names') or [])
            card_name = names[0] if names else None
            localized_texts = source.get('texts') or []
            localized_text = localized_texts[0] if localized_texts else None
            set_names = sorted(source.get('set_names') or data.get('set_names') or [])
            family = data['family']
            canonical_set_name = set_names[0] if set_names else family
            if card_name:
                name_coverage[target] += 1
            if localized_text is not None:
                text_coverage[target] += 1
            source_id = _source_print_id(spec['language'], logical, collector, rarity)
            memberships = []
            for set_name in set_names or [canonical_set_name]:
                release_external = _release_external_id(spec['language'], family, set_name)
                releases.setdefault(release_external, {
                    'external_id': release_external,
                    'name': set_name,
                    'code': family,
                    'language': spec['language'],
                    'region': spec['region'],
                    'metadata': {
                        'source': SOURCE,
                        'source_snapshot': source_meta,
                        'source_set_name': set_name,
                        'source_set_family': family,
                        'source_release_id_kind': 'deterministic-derived-key-no-native-release-id',
                        'economics_imported': False,
                    },
                })
                memberships.append({
                    'release_id': release_external,
                    'source_print_id': source_id,
                    'source_set_name': set_name,
                    'source_set_number': collector,
                    'rarity_raw': sorted(source.get('rarity_raw') or data.get('rarity_raw') or []),
                })
            details = {
                'localized_text': localized_text,
                'source_identity': {
                    'logical_card': logical,
                    'collector': collector,
                    'rarity': rarity,
                    'passwords': sorted(data.get('passwords') or []),
                    'konami_ids': sorted(data.get('konami_ids') or []),
                },
                'source_print_id': source_id,
                'source_print_id_kind': 'deterministic-fine-identity-no-native-physical-uuid',
                'field_provenance': {
                    'physical_identity': SOURCE,
                    'card_name': SOURCE if card_name else None,
                    'localized_text': SOURCE if localized_text is not None else None,
                    'images': None,
                },
                'image_status': 'not-materialized-source-has-no-certified-locale-specific-physical-print-image',
                'generic_english_image_fallback': False,
                'economics_imported': False,
                'source_snapshot': source_meta,
            }
            materializable.append({
                'target': target,
                'language': spec['language'],
                'region': spec['region'],
                'logical_card': logical,
                'collector': collector,
                'rarity': rarity,
                'variant': variant_for_rarity(rarity),
                'print_key': canonical_print_id(target, logical, collector, rarity),
                'card_id': int(card_id),
                'family': family,
                'set_name': canonical_set_name,
                'card_name': card_name,
                'localization_details': _clean(details),
                'images': [],
                'memberships': memberships,
            })

        targets[target] = {
            'language': spec['language'],
            'region': spec['region'],
            'materializable': materializable,
            'retained_missing_card': missing_cards,
            'rarity_disjoint_conflicts': rarity_conflicts,
            'resolution_modes': dict(resolution_modes),
        }
        quarantined[target] = {
            'retained_missing_card': len(missing_cards),
            'rarity_disjoint_conflicts': len(rarity_conflicts),
            'total': len(missing_cards) + len(rarity_conflicts),
        }

    counts = {target: len(data['materializable']) for target, data in targets.items()}
    gates = {
        'exact_overlay_counts': counts == EXPECTED,
        'exact_missing_card_counts': all(quarantined[t]['retained_missing_card'] == EXPECTED_MISSING_CARDS[t] for t in TARGETS),
        'exact_rarity_conflict_counts': all(quarantined[t]['rarity_disjoint_conflicts'] == EXPECTED_RARITY_CONFLICTS[t] for t in TARGETS),
        'quarantine_total_3314': sum(q['total'] for q in quarantined.values()) == 3314,
        'ambiguous_card_resolution_zero': True,
        'source_metadata_has_no_price_keys': not _has_price({'targets': targets, 'releases': releases}),
        'generic_english_image_fallback_zero': True,
        'physical_images_written_zero': all(not item['images'] for d in targets.values() for item in d['materializable']),
    }
    return {
        'game_id': db['game_id'],
        'targets': targets,
        'releases': releases,
        'historical_meta': historical_meta,
        'yaml_stats': yaml_stats,
        'invalid_samples': invalid_samples[:20],
        'source_meta': source_meta,
        'quarantine': quarantined,
        'localized_name_coverage': {t: name_coverage[t] for t in TARGETS},
        'localized_text_coverage': {t: text_coverage[t] for t in TARGETS},
        'gates': gates,
        'structural_pass': all(gates.values()),
        'production_rollout_ready': False,
    }


def apply_plan(plan: dict[str, Any], target_url: str) -> dict[str, Any]:
    conn = psycopg2.connect(target_url, connect_timeout=30, application_name='dontripit_ygo_delta_writer')
    conn.set_session(readonly=False, autocommit=False)
    writes = Counter()
    per_target_prints = Counter()
    try:
        with conn.cursor() as cur:
            game_id = int(plan['game_id'])
            sets = historical_cert._load_sets(cur, game_id)
            for target in TARGETS:
                for item in plan['targets'][target]['materializable']:
                    key = (item['family'], item['region'])
                    if key not in sets:
                        cur.execute(
                            'INSERT INTO sets (game_id,code,region,name,release_date) VALUES (%s,%s,%s,%s,NULL) RETURNING id',
                            (game_id, item['family'], item['region'], item['set_name']),
                        )
                        sets[key] = int(cur.fetchone()[0])
                        writes['sets'] += 1

            cur.execute("SELECT id,external_id FROM catalog_releases WHERE game_id=%s AND source='yaml-yugi'", (game_id,))
            release_ids = {str(ext): int(rid) for rid, ext in cur.fetchall()}
            for ext, release in sorted(plan['releases'].items()):
                if ext not in release_ids:
                    cur.execute(
                        """
                        INSERT INTO catalog_releases
                          (game_id,source,external_id,name,code,release_type,release_date,language,region,metadata_json)
                        VALUES (%s,'yaml-yugi',%s,%s,%s,'localized-physical-current-overlay',NULL,%s,%s,%s) RETURNING id
                        """,
                        (game_id, ext, release['name'], release['code'], release['language'], release['region'], Json(_clean(release['metadata']))),
                    )
                    release_ids[ext] = int(cur.fetchone()[0])
                    writes['catalog_releases'] += 1

            prints = historical_cert._load_prints(cur, game_id)
            print_ids: dict[str, int] = {}
            for target in TARGETS:
                for item in sorted(plan['targets'][target]['materializable'], key=lambda x: x['print_key']):
                    set_id = sets[(item['family'], item['region'])]
                    natural = (item['card_id'], set_id, item['collector'], item['language'], False, item['variant'])
                    found = prints.get(natural)
                    if found is None:
                        cur.execute(
                            """
                            INSERT INTO prints
                              (set_id,card_id,collector_number,language,rarity,is_foil,variant,print_key,scryfall_id,tcgdex_id,yugioh_id,riftbound_id)
                            VALUES (%s,%s,%s,%s,%s,false,%s,%s,NULL,NULL,NULL,NULL) RETURNING id
                            """,
                            (set_id, item['card_id'], item['collector'], item['language'], item['rarity'], item['variant'], item['print_key']),
                        )
                        pid = int(cur.fetchone()[0])
                        prints[natural] = (pid, item['print_key'])
                        writes['prints'] += 1
                        per_target_prints[target] += 1
                    else:
                        pid = int(found[0])
                        if found[1] not in (None, item['print_key']):
                            raise AssertionError(f'Print key conflict for {natural}')
                    print_ids[item['print_key']] = pid

            cur.execute(
                """
                SELECT l.print_id,l.language,l.source,l.external_id,l.card_name,l.set_name,l.details_json
                FROM print_localizations l JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND lower(l.language) IN ('es','ja')
                """,
                (game_id,),
            )
            localizations = {
                (int(pid), str(lang)): (src, ext, name, setname, details)
                for pid, lang, src, ext, name, setname, details in cur.fetchall()
            }
            for target in TARGETS:
                for item in plan['targets'][target]['materializable']:
                    pid = print_ids[item['print_key']]
                    key = (pid, item['language'])
                    expected = _clean(item['localization_details'])
                    existing = localizations.get(key)
                    if existing is None:
                        cur.execute(
                            """
                            INSERT INTO print_localizations
                              (print_id,language,source,external_id,card_name,set_name,details_json)
                            VALUES (%s,%s,'yaml-yugi',%s,%s,%s,%s)
                            """,
                            (pid, item['language'], item['print_key'], item['card_name'], item['set_name'], Json(expected)),
                        )
                        localizations[key] = (SOURCE, item['print_key'], item['card_name'], item['set_name'], expected)
                        writes['print_localizations'] += 1
                    elif existing != (SOURCE, item['print_key'], item['card_name'], item['set_name'], expected):
                        raise AssertionError(f'Localization conflict {item["print_key"]}')

            cur.execute(
                """
                SELECT pr.print_id,cr.external_id,pr.source_print_id,pr.metadata_json
                FROM print_releases pr JOIN catalog_releases cr ON cr.id=pr.release_id
                WHERE cr.game_id=%s AND cr.source='yaml-yugi'
                """,
                (game_id,),
            )
            memberships = {
                (int(pid), str(ext)): (str(srcid) if srcid is not None else None, meta)
                for pid, ext, srcid, meta in cur.fetchall()
            }
            for target in TARGETS:
                for item in plan['targets'][target]['materializable']:
                    pid = print_ids[item['print_key']]
                    for membership in item['memberships']:
                        key = (pid, membership['release_id'])
                        meta = _clean({
                            'source': SOURCE,
                            'source_set_name': membership['source_set_name'],
                            'source_set_number': membership['source_set_number'],
                            'rarity_raw': membership['rarity_raw'],
                            'image_urls': [],
                            'economics_imported': False,
                            'source_print_id_kind': 'deterministic-fine-identity-no-native-physical-uuid',
                        })
                        existing = memberships.get(key)
                        if existing is None:
                            cur.execute(
                                """
                                INSERT INTO print_releases
                                  (print_id,release_id,source_print_id,appearance_type,metadata_json)
                                VALUES (%s,%s,%s,'localized-physical-current-overlay',%s)
                                """,
                                (pid, release_ids[membership['release_id']], membership['source_print_id'], Json(meta)),
                            )
                            memberships[key] = (membership['source_print_id'], meta)
                            writes['print_releases'] += 1
                        elif existing != (membership['source_print_id'], meta):
                            raise AssertionError(f'PrintRelease provenance conflict {key}')

            conn.commit()
            return {
                'writes': dict(sorted(writes.items())),
                'total_writes': sum(writes.values()),
                'prints_created_by_target': dict(per_target_prints),
                'prints_created': writes['prints'],
                'materialized_print_ids': len(print_ids),
            }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def validate(plan: dict[str, Any], target_url: str, baseline: dict[str, Any]) -> dict[str, Any]:
    lean = validate_lean_invariants(target_url, baseline)
    conn = psycopg2.connect(target_url, connect_timeout=30, application_name='dontripit_ygo_delta_validate')
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            game_id = int(plan['game_id'])
            sets = historical_cert._load_sets(cur, game_id)
            prints = historical_cert._load_prints(cur, game_id)
            ids = []
            missing = []
            per_target = {}
            for target in TARGETS:
                found_count = 0
                for item in plan['targets'][target]['materializable']:
                    sid = sets.get((item['family'], item['region']))
                    natural = (item['card_id'], sid, item['collector'], item['language'], False, item['variant']) if sid else None
                    found = prints.get(natural) if natural else None
                    if not found:
                        missing.append(item['print_key'])
                        continue
                    ids.append(int(found[0]))
                    found_count += 1
                per_target[target] = found_count
            unique_ids = sorted(set(ids))
            cur.execute(
                "SELECT count(*) FROM prints WHERE id=ANY(%s) AND (scryfall_id IS NOT NULL OR tcgdex_id IS NOT NULL OR yugioh_id IS NOT NULL OR riftbound_id IS NOT NULL)",
                (unique_ids or [-1],),
            )
            foreign_ids = int(cur.fetchone()[0])
            cur.execute("SELECT count(*) FROM print_images WHERE print_id=ANY(%s)", (unique_ids or [-1],))
            images = int(cur.fetchone()[0])
            cur.execute("SELECT details_json FROM print_localizations WHERE print_id=ANY(%s) AND source='yaml-yugi'", (unique_ids or [-1],))
            locs = [row[0] for row in cur.fetchall()]
            cur.execute(
                """
                SELECT pr.source_print_id,pr.metadata_json FROM print_releases pr
                JOIN catalog_releases cr ON cr.id=pr.release_id
                WHERE pr.print_id=ANY(%s) AND cr.source='yaml-yugi'
                """,
                (unique_ids or [-1],),
            )
            memberships = cur.fetchall()
            metadata_price = sum(_has_price(v) for v in locs) + sum(_has_price(meta) for _sid, meta in memberships)
            provenance_missing = sum(not s(srcid).startswith('yaml-yugi:') for srcid, _meta in memberships)
            cur.execute(
                """
                SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('es','ja') AND p.id=ANY(%s)
                """,
                (game_id, unique_ids or [-1]),
            )
            target_print_rows = int(cur.fetchone()[0])
            conn.rollback()

        gates = {
            'all_9288_prints_materialized': len(unique_ids) == 9288 and not missing and target_print_rows == 9288,
            'exact_target_counts': per_target == EXPECTED,
            'foreign_global_ids_null': foreign_ids == 0,
            'no_physical_images_without_certified_source': images == 0,
            'metadata_price_keys_zero': metadata_price == 0,
            'print_release_provenance_present': provenance_missing == 0 and bool(memberships),
            'preexisting_catalog_and_economics_unchanged': lean['status'] == 'pass',
        }
        return {
            'status': 'pass' if all(gates.values()) else 'fail',
            'gates': gates,
            'target_counts': per_target,
            'materialized_unique_prints': len(unique_ids),
            'missing_prints': missing[:50],
            'foreign_id_violations': foreign_ids,
            'print_images_written': images,
            'membership_rows': len(memberships),
            'provenance_missing': provenance_missing,
            'metadata_price_key_violations': metadata_price,
            'lean_invariants': lean,
        }
    finally:
        conn.close()


def run(root: Path, cards_path: Path, source_meta_path: Path, output: Path) -> dict[str, Any]:
    target_url = os.getenv('EPHEMERAL_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not target_url:
        raise RuntimeError('EPHEMERAL_DATABASE_URL or DATABASE_URL required')
    baseline = json.loads(BASELINE_PATH.read_text(encoding='utf-8'))
    source_meta = json.loads(source_meta_path.read_text(encoding='utf-8'))
    conn = psycopg2.connect(target_url, connect_timeout=30)
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT version_num FROM alembic_version LIMIT 1')
            revision = str(cur.fetchone()[0])
            if revision != EXPECTED_HEAD:
                raise RuntimeError(f'Expected {EXPECTED_HEAD}, got {revision}')
            cur.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
            game_id = int(cur.fetchone()[0])
            conn.rollback()
    finally:
        conn.close()

    _install_delta_guards(target_url, game_id)
    plan = build_plan(root, cards_path, source_meta, target_url)
    if not plan['structural_pass']:
        report = {
            'status': 'fail',
            'mode': 'ephemeral-only-yugioh-current-physical-overlay-v1',
            'production_writes': 0,
            'plan': plan,
            'production_rollout_ready': False,
        }
    else:
        pass1 = apply_plan(plan, target_url)
        validation = validate(plan, target_url, baseline)
        pass2 = apply_plan(plan, target_url)
        pass2_zero = pass2['total_writes'] == 0
        pass1_exact = pass1['prints_created'] == 9288 and pass1['prints_created_by_target'] == EXPECTED
        status = 'pass' if validation['status'] == 'pass' and pass2_zero and pass1_exact else 'fail'
        report = {
            'status': status,
            'mode': 'ephemeral-only-yugioh-current-physical-overlay-v1',
            'production_writes': 0,
            'source_meta': source_meta,
            'plan_summary': {
                'overlay_counts': {t: len(plan['targets'][t]['materializable']) for t in TARGETS},
                'quarantine': plan['quarantine'],
                'localized_name_coverage': plan['localized_name_coverage'],
                'localized_text_coverage': plan['localized_text_coverage'],
                'gates': plan['gates'],
                'release_count': len(plan['releases']),
            },
            'pass1': pass1,
            'pass1_exact_9288': pass1_exact,
            'validation': validation,
            'pass2': pass2,
            'pass2_zero_writes': pass2_zero,
            'production_rollout_ready': False,
            'rollout_blocker': 'YGOJSON physical source freshness remains 2026-04-07; current YAML overlay has no native physical UUID/image linkage',
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str, sort_keys=True))
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--ygojson-dir', type=Path, required=True)
    ap.add_argument('--yaml-cards', type=Path, required=True)
    ap.add_argument('--yaml-meta', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    return 0 if run(args.ygojson_dir, args.yaml_cards, args.yaml_meta, args.output)['status'] == 'pass' else 2


if __name__ == '__main__':
    raise SystemExit(main())
