from __future__ import annotations

# Explicit production retrigger after guarded writer installation.
from app.scripts import apply_yugioh_ocg_calibrated_variants54_v2 as base

base.METHOD='cardmarket_ocg_certified_independent_ordinal_rarity_next223_v2'
base.CONFIRM='APPLY_YUGIOH_OCG_NEXT223_CALIBRATED118_V2'
base.STABLE_IDENTITY_SHA256='75b1645c668e1eec2bd79f58e23636d8cddb463ea59093bf34789700ae19d823'
base.TARGETS={
 'SD45':('5211',10),
 'DBDS':('4625',6),
 'DBGC':('4526',6),
 'DBHS':('4610',6),
 'DBIC':('4590',6),
 'DBMF':('4575',6),
 'DBSS':('4558',6),
 '21PP':('4541',16),
 '22PP':('4521',16),
 'PP19':('4650',40),
}
base.ALLOWED={
 ('secret','super'):('super','secret'),
 ('secret','ultra'):('ultra','secret'),
 ('common','secret'):('common','secret'),
}
base.EXPECTED_TOTAL=118

if __name__=='__main__':
 raise SystemExit(base.main())
