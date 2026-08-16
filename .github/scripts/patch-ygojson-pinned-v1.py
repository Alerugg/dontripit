from pathlib import Path

# Guard the two known YGOPRODeck payload defects on the pinned YGOJSON revision.
p = Path('ygojson-src/src/ygojson/importers/ygoprodeck.py')
text = p.read_text(encoding='utf-8')
replacements = [
    (
        'Attribute(in_json["attribute"].lower()) if "attribute" in in_json else None',
        'Attribute(in_json["attribute"].lower()) if in_json.get("attribute") else None',
        'attribute null guard',
    ),
    (
        'card.type = Race(in_json["race"].lower().replace("-", "").replace(" ", ""))',
        'card.type = Race(in_json["race"].lower().replace("-", "").replace(" ", "")) if in_json.get("race") else None',
        'race empty guard',
    ),
]
for old, new, label in replacements:
    if text.count(old) != 1 or text.count(new) != 0:
        raise AssertionError(f'Pinned upstream line changed for {label}; refuse blind patch')
    text = text.replace(old, new)
    print(f'patched {label}')
p.write_text(text, encoding='utf-8')

# Yugipedia sometimes returns HTTP 200 with an API-level transient database error.
# Upstream retries transport failures, but not this response shape. Retry ONLY the
# known DBConnectionError and keep every other API/data/schema error fail-closed.
p = Path('ygojson-src/src/ygojson/importers/yugipedia.py')
text = p.read_text(encoding='utf-8')
old = '''def paginate_query(query) -> typing.Iterable:\n    query = query.copy()\n    while True:\n        in_json = make_request(query).json()\n        if "query" not in in_json:\n            raise ValueError(\n                f"Got bad JSON: {json.dumps(in_json)} from query: {json.dumps(query)}"\n            )\n        yield in_json["query"]\n        if "continue" in in_json:\n            query.update(in_json["continue"])\n        else:\n            break\n'''
new = '''def paginate_query(query) -> typing.Iterable:\n    query = query.copy()\n    transient_db_retries = 0\n    while True:\n        in_json = make_request(query).json()\n        if "query" not in in_json:\n            error = in_json.get("error") if isinstance(in_json, dict) else None\n            if (\n                isinstance(error, dict)\n                and error.get("code") == "internal_api_error_DBConnectionError"\n                and transient_db_retries < 8\n            ):\n                transient_db_retries += 1\n                wait_seconds = min(60.0, RATE_LIMIT * 5 * transient_db_retries)\n                logging.warning(\n                    "Yugipedia transient DBConnectionError; retry %s/8 in %.1fs",\n                    transient_db_retries,\n                    wait_seconds,\n                )\n                time.sleep(wait_seconds)\n                continue\n            raise ValueError(\n                f"Got bad JSON: {json.dumps(in_json)} from query: {json.dumps(query)}"\n            )\n        transient_db_retries = 0\n        yield in_json["query"]\n        if "continue" in in_json:\n            query.update(in_json["continue"])\n        else:\n            break\n'''
if text.count(old) != 1 or text.count(new) != 0:
    raise AssertionError('Pinned upstream paginate_query changed; refuse blind transient retry patch')
text = text.replace(old, new)
p.write_text(text, encoding='utf-8')
print('patched Yugipedia transient DBConnectionError retry guard')
