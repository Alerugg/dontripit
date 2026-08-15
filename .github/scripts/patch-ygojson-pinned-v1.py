from pathlib import Path

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
