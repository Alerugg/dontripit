from __future__ import annotations

import hashlib
import json

CONTRACT_SHA256 = '86e1e0fa94e6bb22a4640513cd166907904b25c7ff6b5f6f3ac2ef68d28eb23c'
PAIR_MANIFEST_SHA256 = '359799b8b165d6974ab703e4b18026b350cfb88d635acf6a765023759b8486f2'
EXPECTED_PAIRS = 41
EXPECTED_BY_SET = {'DOCS': 19, 'LTGY': 11, 'CSOC': 11}
EXPECTED_UNSUPPORTED = {'DOCS': 20, 'LTGY': 0, 'CSOC': 2}
FIELDS = ('set_code','idExpansion','idMetacard','idProduct','external_product_id','print_id','card_id','collector_number','canonical_rarity','canonical_variant','product_ordinal','contract_rarity')
PAIR_TUPLES = [
    ('DOCS', '4680', '220983', '693616', 174896, 700565, 67359, 'DOCS-JP042', 'ultra', 'rarity-ultra', 1, 'ultra'),
    ('DOCS', '4680', '220983', '693617', 174897, 687242, 67359, 'DOCS-JP042', 'secret', 'rarity-secret', 2, 'secret'),
    ('DOCS', '4680', '220983', '693618', 174898, 681874, 67359, 'DOCS-JP042', 'ultimate', 'rarity-ultimate', 3, 'ultimate'),
    ('DOCS', '4680', '220986', '693622', 174902, 695441, 67172, 'DOCS-JP045', 'ultra', 'rarity-ultra', 1, 'ultra'),
    ('DOCS', '4680', '220986', '693623', 174903, 666610, 67172, 'DOCS-JP045', 'secret', 'rarity-secret', 2, 'secret'),
    ('DOCS', '4680', '220986', '693624', 174904, 696946, 67172, 'DOCS-JP045', 'ultimate', 'rarity-ultimate', 3, 'ultimate'),
    ('DOCS', '4680', '220987', '693625', 174905, 677150, 71052, 'DOCS-JP046', 'ultra', 'rarity-ultra', 1, 'ultra'),
    ('DOCS', '4680', '220987', '693626', 174906, 672437, 71052, 'DOCS-JP046', 'secret', 'rarity-secret', 2, 'secret'),
    ('DOCS', '4680', '220987', '693627', 174907, 670265, 71052, 'DOCS-JP046', 'ultimate', 'rarity-ultimate', 3, 'ultimate'),
    ('DOCS', '4680', '220987', '693628', 174908, 675154, 71052, 'DOCS-JP046', 'ghost', 'rarity-ghost', 4, 'ghost'),
    ('DOCS', '4680', '220988', '693629', 174909, 669081, 61774, 'DOCS-JP047', 'ultra', 'rarity-ultra', 1, 'ultra'),
    ('DOCS', '4680', '220988', '693630', 174910, 678788, 61774, 'DOCS-JP047', 'secret', 'rarity-secret', 2, 'secret'),
    ('DOCS', '4680', '220988', '693631', 174911, 672341, 61774, 'DOCS-JP047', 'ultimate', 'rarity-ultimate', 3, 'ultimate'),
    ('DOCS', '4680', '220991', '693635', 174915, 699712, 61753, 'DOCS-JP050', 'ultra', 'rarity-ultra', 1, 'ultra'),
    ('DOCS', '4680', '220991', '693636', 174916, 676521, 61753, 'DOCS-JP050', 'secret', 'rarity-secret', 2, 'secret'),
    ('DOCS', '4680', '220991', '693637', 174917, 672563, 61753, 'DOCS-JP050', 'ultimate', 'rarity-ultimate', 3, 'ultimate'),
    ('DOCS', '4680', '220993', '693639', 174919, 682536, 72179, 'DOCS-JP052', 'ultra', 'rarity-ultra', 1, 'ultra'),
    ('DOCS', '4680', '220993', '693640', 174920, 683460, 72179, 'DOCS-JP052', 'secret', 'rarity-secret', 2, 'secret'),
    ('DOCS', '4680', '220993', '693641', 174921, 702487, 72179, 'DOCS-JP052', 'ultimate', 'rarity-ultimate', 3, 'ultimate'),
    ('LTGY', '4725', '208045', '704690', 178583, 693716, 72110, 'LTGY-JP044', 'ultra', 'rarity-ultra', 1, 'ultra'),
    ('LTGY', '4725', '208045', '704691', 178584, 700419, 72110, 'LTGY-JP044', 'ultimate', 'rarity-ultimate', 2, 'ultimate'),
    ('LTGY', '4725', '208045', '704692', 178585, 680443, 72110, 'LTGY-JP044', 'ghost', 'rarity-ghost', 3, 'ghost'),
    ('LTGY', '4725', '208052', '704699', 178592, 679648, 68118, 'LTGY-JP051', 'ultra', 'rarity-ultra', 1, 'ultra'),
    ('LTGY', '4725', '208052', '704700', 178593, 679523, 68118, 'LTGY-JP051', 'ultimate', 'rarity-ultimate', 2, 'ultimate'),
    ('LTGY', '4725', '208053', '704701', 178594, 687084, 71710, 'LTGY-JP052', 'ultra', 'rarity-ultra', 1, 'ultra'),
    ('LTGY', '4725', '208053', '704702', 178595, 697327, 71710, 'LTGY-JP052', 'ultimate', 'rarity-ultimate', 2, 'ultimate'),
    ('LTGY', '4725', '208054', '704703', 178596, 687375, 62662, 'LTGY-JP053', 'ultra', 'rarity-ultra', 1, 'ultra'),
    ('LTGY', '4725', '208054', '704704', 178597, 696526, 62662, 'LTGY-JP053', 'ultimate', 'rarity-ultimate', 2, 'ultimate'),
    ('LTGY', '4725', '208061', '704711', 178604, 687575, 66401, 'LTGY-JP060', 'ultra', 'rarity-ultra', 1, 'ultra'),
    ('LTGY', '4725', '208061', '704712', 178605, 676669, 66401, 'LTGY-JP060', 'ultimate', 'rarity-ultimate', 2, 'ultimate'),
    ('CSOC', '4809', '105584', '713512', 183664, 694997, 66198, 'CSOC-JP038', 'ultra', 'rarity-ultra', 1, 'ultra'),
    ('CSOC', '4809', '105584', '713513', 183665, 696835, 66198, 'CSOC-JP038', 'ultimate', 'rarity-ultimate', 2, 'ultimate'),
    ('CSOC', '4809', '105585', '713514', 183666, 669739, 70086, 'CSOC-JP039', 'ultra', 'rarity-ultra', 1, 'ultra'),
    ('CSOC', '4809', '105585', '713515', 183667, 689256, 70086, 'CSOC-JP039', 'ultimate', 'rarity-ultimate', 2, 'ultimate'),
    ('CSOC', '4809', '105585', '713516', 183668, 700228, 70086, 'CSOC-JP039', 'ghost', 'rarity-ghost', 3, 'ghost'),
    ('CSOC', '4809', '105589', '713520', 183672, 671058, 60316, 'CSOC-JP043', 'ultra', 'rarity-ultra', 1, 'ultra'),
    ('CSOC', '4809', '105589', '713521', 183673, 677658, 60316, 'CSOC-JP043', 'ultimate', 'rarity-ultimate', 2, 'ultimate'),
    ('CSOC', '4809', '105590', '713522', 183674, 666557, 60216, 'CSOC-JP044', 'ultra', 'rarity-ultra', 1, 'ultra'),
    ('CSOC', '4809', '105590', '713523', 183675, 696193, 60216, 'CSOC-JP044', 'ultimate', 'rarity-ultimate', 2, 'ultimate'),
    ('CSOC', '4809', '105611', '713544', 183696, 674545, 73004, 'CSOC-JP065', 'ultra', 'rarity-ultra', 1, 'ultra'),
    ('CSOC', '4809', '105611', '713545', 183697, 674448, 73004, 'CSOC-JP065', 'ultimate', 'rarity-ultimate', 2, 'ultimate'),
]

def pairs() -> list[dict]:
    return [dict(zip(FIELDS, row, strict=True)) for row in PAIR_TUPLES]

def manifest_sha256() -> str:
    raw = json.dumps(PAIR_TUPLES, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()
