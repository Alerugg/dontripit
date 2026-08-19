from __future__ import annotations

import hashlib
import json

EVIDENCE_SHA256 = '73ad75d60ca1ab0c2fa4c647d98f8e9bc1e8be4b75f1a911ac6085fd8852dd0f'
PAIR_MANIFEST_SHA256 = 'e2df7294e917f4b07a8f054775273715169ec70f2acf59691bc0b8fcf9c4e268'
EXPECTED_PAIRS = 20
EXPECTED_GROUPS = 10
EXPECTED_METACARDS = ('220961','220978','220985','220989','221004','221006','221017','221041','221393','225872')
FIELDS = ('set_code','idExpansion','idMetacard','idProduct','external_product_id','print_id','card_id','collector_number','canonical_rarity','canonical_variant','product_ordinal','contract_rarity')
PAIR_TUPLES = [
    ('DOCS','4680','220961','693590',174870,702298,60575,'DOCS-JP019','super','rarity-super',1,'super'),
    ('DOCS','4680','220961','693591',174871,669059,60575,'DOCS-JP019','secret','rarity-secret',2,'secret'),
    ('DOCS','4680','220978','693610',174890,676595,72725,'DOCS-JP037','super','rarity-super',1,'super'),
    ('DOCS','4680','220978','693611',174891,672997,72725,'DOCS-JP037','secret','rarity-secret',2,'secret'),
    ('DOCS','4680','220985','693620',174900,697568,63477,'DOCS-JP044','super','rarity-super',1,'super'),
    ('DOCS','4680','220985','693621',174901,666721,63477,'DOCS-JP044','secret','rarity-secret',2,'secret'),
    ('DOCS','4680','220989','693632',174912,672665,67014,'DOCS-JP048','super','rarity-super',1,'super'),
    ('DOCS','4680','220989','693633',174913,673869,67014,'DOCS-JP048','secret','rarity-secret',2,'secret'),
    ('DOCS','4680','221004','693652',174932,679046,66467,'DOCS-JP063','super','rarity-super',1,'super'),
    ('DOCS','4680','221004','693653',174933,694200,66467,'DOCS-JP063','secret','rarity-secret',2,'secret'),
    ('DOCS','4680','221006','693655',174935,668807,59574,'DOCS-JP065','super','rarity-super',1,'super'),
    ('DOCS','4680','221006','693656',174936,691136,59574,'DOCS-JP065','secret','rarity-secret',2,'secret'),
    ('DOCS','4680','221017','693667',174947,667308,70316,'DOCS-JP076','super','rarity-super',1,'super'),
    ('DOCS','4680','221017','693668',174948,669244,70316,'DOCS-JP076','secret','rarity-secret',2,'secret'),
    ('DOCS','4680','221041','693596',174876,670448,69498,'DOCS-JP024','super','rarity-super',1,'super'),
    ('DOCS','4680','221041','693597',174877,670619,69498,'DOCS-JP024','secret','rarity-secret',2,'secret'),
    ('DOCS','4680','221393','693675',174955,697365,60393,'DOCS-JP082','super','rarity-super',1,'super'),
    ('DOCS','4680','221393','693676',174956,702029,60393,'DOCS-JP082','secret','rarity-secret',2,'secret'),
    ('DOCS','4680','225872','693673',174953,679721,69571,'DOCS-JP081','super','rarity-super',1,'super'),
    ('DOCS','4680','225872','693674',174954,670382,69571,'DOCS-JP081','secret','rarity-secret',2,'secret'),
]


def pairs() -> list[dict]:
    return [dict(zip(FIELDS,row,strict=True)) for row in PAIR_TUPLES]


def manifest_sha256() -> str:
    raw=json.dumps(PAIR_TUPLES,separators=(',',':'),ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()
