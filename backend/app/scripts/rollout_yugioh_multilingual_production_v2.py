from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.scripts import rollout_yugioh_multilingual_production_v1 as base


def _non_ygo_counts(cur, game_id: int) -> dict[str, dict[str, int]]:
    """Audit other games without multiplying Sets x Cards x Prints."""
    cur.execute(
        '''
        SELECT g.slug,
               (SELECT COUNT(*)::bigint FROM sets s WHERE s.game_id=g.id),
               (SELECT COUNT(*)::bigint FROM cards c WHERE c.game_id=g.id),
               (
                 SELECT COUNT(*)::bigint
                 FROM prints p JOIN cards c2 ON c2.id=p.card_id
                 WHERE c2.game_id=g.id
               )
        FROM games g
        WHERE g.id<>%s
        ORDER BY g.slug
        ''',
        (game_id,),
    )
    return {
        str(slug): {'sets': int(sets), 'cards': int(cards), 'prints': int(prints)}
        for slug, sets, cards, prints in cur.fetchall()
    }


# _snapshot() resolves this symbol from the base module at call time.
base._non_ygo_counts = _non_ygo_counts


def run(**kwargs: Any) -> dict[str, Any]:
    output: Path = kwargs['output']
    apply = bool(kwargs['apply'])
    report = base.run(**kwargs)
    report['runner_version'] = 2
    report['commit_confirmed'] = apply and report.get('status') == 'pass'
    # base.run() returns only after psycopg2 commit() has completed. Rewriting here
    # guarantees that a PASS artifact carrying commit_confirmed=true is post-commit.
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--yaml-yugi-cards', type=Path, required=True)
    parser.add_argument('--yaml-yugi-meta', type=Path, required=True)
    parser.add_argument('--fresh-certificate', type=Path, required=True)
    parser.add_argument('--readiness', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    report = run(
        input_dir=args.input_dir,
        yaml_cards=args.yaml_yugi_cards,
        yaml_meta=args.yaml_yugi_meta,
        fresh_certificate=args.fresh_certificate,
        readiness_path=args.readiness,
        output=args.output,
        apply=args.apply,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str, sort_keys=True))
    return 0 if report.get('status') == 'pass' else 2


if __name__ == '__main__':
    raise SystemExit(main())
