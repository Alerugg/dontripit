from __future__ import annotations

import os
from app.scripts import audit_yugioh_ocg_singleton_heavy_cohort_v1 as base

base.TARGETS={
 'ST13':{'idExpansion':'1445','physical':57,'logical':57},
 'AC01':{'idExpansion':'4532','physical':98,'logical':50},
 'AC02':{'idExpansion':'5081','physical':98,'logical':50},
 'DP24':{'idExpansion':'3334','physical':58,'logical':46},
 'SD45':{'idExpansion':'5211','physical':53,'logical':46},
 'CPD1':{'idExpansion':'4684','physical':54,'logical':45},
 'DBDS':{'idExpansion':'4625','physical':78,'logical':45},
 'DBGC':{'idExpansion':'4526','physical':78,'logical':45},
 'DBHS':{'idExpansion':'4610','physical':78,'logical':45},
 'DBIC':{'idExpansion':'4590','physical':78,'logical':45},
 'DBMF':{'idExpansion':'4575','physical':78,'logical':45},
 'DBSS':{'idExpansion':'4558','physical':78,'logical':45},
 'SPDS':{'idExpansion':'4658','physical':75,'logical':45},
 'SPHR':{'idExpansion':'4679','physical':75,'logical':45},
 'SPWR':{'idExpansion':'4676','physical':75,'logical':45},
 'YSD4':{'idExpansion':'1172','physical':43,'logical':43},
 'SR07':{'idExpansion':'2365','physical':42,'logical':42},
 'SR08':{'idExpansion':'2435','physical':41,'logical':41},
 '21PP':{'idExpansion':'4541','physical':64,'logical':32},
 '22PP':{'idExpansion':'4521','physical':64,'logical':32},
 'DC01':{'idExpansion':'4706','physical':67,'logical':30},
 'GS02':{'idExpansion':'4785','physical':40,'logical':20},
 'GS03':{'idExpansion':'4766','physical':40,'logical':20},
 'GS04':{'idExpansion':'4746','physical':40,'logical':20},
 'GS06':{'idExpansion':'4710','physical':50,'logical':20},
 'PP19':{'idExpansion':'4650','physical':40,'logical':20},
}

if __name__=='__main__':
 os.environ.setdefault('YGO_OCG_SINGLETON_HEAVY_COHORT_OUTPUT','/tmp/ygo-ocg-next-singleton-heavy-cohort-v2.json')
 raise SystemExit(base.main())
