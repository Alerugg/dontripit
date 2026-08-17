#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

WORKFLOW_SUFFIXES = {'.yml', '.yaml'}
DB_TOKENS = ('DATABASE_URL', 'DATABASE_URL_UNPOOLED', 'NEON_DATABASE_URL')
WRITE_TOKENS = (
    '--apply',
    'alembic upgrade',
    'session.commit()',
    '.commit()',
    'delete from ',
    'insert into ',
    'update ',
    'truncate ',
    'drop table',
    'create table',
    'psql ',
)
DEPLOY_TOKENS = (
    'vercel', 'render deploy', 'fly deploy', 'railway', 'docker push',
    'deploy-pages', 'deployment',
)


def _on_block(text: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r'^on\s*:\s*(.*)$', line)
        if not match:
            continue
        inline = match.group(1).strip()
        if inline:
            return inline
        block = []
        for child in lines[index + 1:]:
            if child.strip() and not child.startswith((' ', '\t', '#')):
                break
            block.append(child)
        return '\n'.join(block)
    return ''


def _event_present(block: str, event: str) -> bool:
    if not block:
        return False
    if block.lstrip().startswith('['):
        return bool(re.search(rf'\b{re.escape(event)}\b', block))
    if '\n' not in block and ':' not in block:
        return block.strip() == event
    return bool(re.search(rf'^\s{{2}}{re.escape(event)}\s*:', block, re.M))


def _event_subblock(block: str, event: str) -> str:
    lines = block.splitlines()
    start = None
    event_indent = None
    for i, line in enumerate(lines):
        m = re.match(r'^(\s+)' + re.escape(event) + r'\s*:\s*(.*)$', line)
        if m:
            start = i
            event_indent = len(m.group(1))
            if m.group(2).strip():
                return m.group(2).strip()
            break
    if start is None:
        return ''
    out = []
    for line in lines[start + 1:]:
        if line.strip():
            indent = len(line) - len(line.lstrip())
            if indent <= event_indent:
                break
        out.append(line)
    return '\n'.join(out)


def _push_matches_main(block: str) -> bool:
    if not _event_present(block, 'push'):
        return False
    # Inline/on: push has no branch filter, so it matches main.
    if block.lstrip().startswith('[') or ('\n' not in block and block.strip() == 'push'):
        return True
    push = _event_subblock(block, 'push')
    if not push.strip():
        return True
    if 'branches-ignore:' in push and 'branches:' not in push:
        ignored = _branch_values(push, 'branches-ignore')
        return not any(_branch_pattern_matches_main(value) for value in ignored)
    branches = _branch_values(push, 'branches')
    if not branches:
        return True
    return any(_branch_pattern_matches_main(value) for value in branches)


def _branch_values(block: str, key: str) -> list[str]:
    values: list[str] = []
    inline = re.search(rf'{re.escape(key)}\s*:\s*\[([^\]]*)\]', block)
    if inline:
        values.extend(item.strip().strip('"\'') for item in inline.group(1).split(',') if item.strip())
        return values
    lines = block.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r'^(\s*)' + re.escape(key) + r'\s*:\s*(.*)$', line)
        if not m:
            continue
        base_indent = len(m.group(1))
        if m.group(2).strip():
            values.append(m.group(2).strip().strip('"\''))
            continue
        for child in lines[i + 1:]:
            if child.strip():
                indent = len(child) - len(child.lstrip())
                if indent <= base_indent:
                    break
                item = re.match(r'^\s*-\s*(.+?)\s*$', child)
                if item:
                    values.append(item.group(1).strip().strip('"\''))
        break
    return values


def _branch_pattern_matches_main(value: str) -> bool:
    value = value.strip()
    if value in {'main', '*', '**'}:
        return True
    # Conservative glob approximation.
    regex = '^' + re.escape(value).replace(r'\*\*', '.*').replace(r'\*', '[^/]*') + '$'
    try:
        return bool(re.match(regex, 'main'))
    except re.error:
        return True


def _classify(root: Path) -> dict:
    workflow_dir = root / '.github' / 'workflows'
    entries = []
    failures = []
    if not workflow_dir.exists():
        return {'workflow_count': 0, 'entries': [], 'failures': ['missing_workflow_directory']}

    for path in sorted(workflow_dir.iterdir()):
        if path.suffix not in WORKFLOW_SUFFIXES or not path.is_file():
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        lower = text.casefold()
        on_block = _on_block(text)
        has_push = _event_present(on_block, 'push')
        push_main = _push_matches_main(on_block)
        has_schedule = _event_present(on_block, 'schedule')
        has_dispatch = _event_present(on_block, 'workflow_dispatch')
        has_db = any(token.casefold() in lower for token in DB_TOKENS)
        write_signals = sorted({token for token in WRITE_TOKENS if token in lower})
        deploy_signals = sorted({token for token in DEPLOY_TOKENS if token in lower})
        db_writer = has_db and bool(write_signals)
        rel = str(path.relative_to(root))
        entry = {
            'path': rel,
            'push': has_push,
            'push_matches_main': push_main,
            'schedule': has_schedule,
            'workflow_dispatch': has_dispatch,
            'database_capable': has_db,
            'write_signals': write_signals,
            'database_writer': db_writer,
            'deploy_signals': deploy_signals,
        }
        entries.append(entry)

        # A merge to main must never trigger a workflow capable of mutating the
        # production database. Scheduled production ownership is intentionally
        # centralized in the daily orchestrator.
        if push_main and db_writer:
            failures.append({'unsafe_main_push_database_writer': rel})
        if has_schedule and db_writer and not rel.endswith('/daily-catalog-v2-orchestrator.yml'):
            failures.append({'independent_scheduled_database_writer': rel})

    scheduled = [e['path'] for e in entries if e['schedule']]
    main_push = [e['path'] for e in entries if e['push_matches_main']]
    db_writers = [e['path'] for e in entries if e['database_writer']]
    deploys = [e['path'] for e in entries if e['deploy_signals']]
    return {
        'workflow_count': len(entries),
        'scheduled': scheduled,
        'main_push': main_push,
        'database_writers': db_writers,
        'deployment_related': deploys,
        'entries': entries,
        'failures': failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--catalog-root', default='.')
    parser.add_argument('--main-root', required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args()

    catalog = _classify(Path(args.catalog_root))
    main_snapshot = _classify(Path(args.main_root))
    failures = list(catalog['failures'])

    orchestrator = Path(args.catalog_root) / '.github/workflows/daily-catalog-v2-orchestrator.yml'
    if not orchestrator.exists():
        failures.append({'missing_catalog_orchestrator': str(orchestrator)})
    else:
        text = orchestrator.read_text(encoding='utf-8')
        block = _on_block(text)
        if not _event_present(block, 'schedule') or not _event_present(block, 'workflow_dispatch'):
            failures.append({'orchestrator_contract_invalid': 'missing schedule or workflow_dispatch'})
        if _event_present(block, 'push'):
            failures.append({'orchestrator_contract_invalid': 'push trigger present'})

    report = {
        'gate': 'PASS' if not failures else 'FAIL',
        'catalog_v2': catalog,
        'main_before_promotion': main_snapshot,
        'promotion_failures': failures,
        'notes': {
            'main_push_database_writers_must_be_zero': True,
            'independent_scheduled_database_writers_must_be_zero': True,
            'external_host_git_integrations': 'not observable from repository workflow files; deployment-related files are inventoried separately',
        },
    }
    Path(args.report).write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('MAIN_PROMOTION_SAFETY ' + json.dumps({
        'gate': report['gate'],
        'catalog_workflows': catalog['workflow_count'],
        'catalog_scheduled': catalog['scheduled'],
        'catalog_main_push': catalog['main_push'],
        'catalog_database_writers': catalog['database_writers'],
        'catalog_deployment_related': catalog['deployment_related'],
        'main_before_scheduled': main_snapshot['scheduled'],
        'main_before_main_push': main_snapshot['main_push'],
        'failures': failures,
    }, sort_keys=True))
    return 0 if report['gate'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
