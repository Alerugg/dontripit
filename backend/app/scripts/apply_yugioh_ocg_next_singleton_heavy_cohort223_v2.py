from __future__ import annotations

from app.scripts import apply_yugioh_ocg_singleton_heavy_cohort_v1 as base

base.METHOD='cardmarket_ocg_certified_singleton_heavy_cohort_v2'
base.CONFIRM='APPLY_YUGIOH_OCG_NEXT_SINGLETON_HEAVY_COHORT223_V2'
base.STABLE_IDENTITY_SHA256='059fb9ad22dc29c20ef16cbc67bc30b2ca7bc4be7e0a6b9c7798ecdc24387a60'
base.TARGETS={
 'AC01':{'idExpansion':'4532','physical':98,'logical':50,'singletons':2},
 'AC02':{'idExpansion':'5081','physical':98,'logical':50,'singletons':2},
 'SD45':{'idExpansion':'5211','physical':53,'logical':46,'singletons':39},
 'CPD1':{'idExpansion':'4684','physical':54,'logical':45,'singletons':36},
 'DBDS':{'idExpansion':'4625','physical':78,'logical':45,'singletons':12},
 'DBGC':{'idExpansion':'4526','physical':78,'logical':45,'singletons':12},
 'DBHS':{'idExpansion':'4610','physical':78,'logical':45,'singletons':12},
 'DBIC':{'idExpansion':'4590','physical':78,'logical':45,'singletons':12},
 'DBMF':{'idExpansion':'4575','physical':78,'logical':45,'singletons':12},
 'DBSS':{'idExpansion':'4558','physical':78,'logical':45,'singletons':12},
 'SPDS':{'idExpansion':'4658','physical':75,'logical':45,'singletons':15},
 'SPHR':{'idExpansion':'4679','physical':75,'logical':45,'singletons':15},
 'SPWR':{'idExpansion':'4676','physical':75,'logical':45,'singletons':15},
 '21PP':{'idExpansion':'4541','physical':64,'logical':32,'singletons':12},
 '22PP':{'idExpansion':'4521','physical':64,'logical':32,'singletons':12},
 'DC01':{'idExpansion':'4706','physical':67,'logical':30,'singletons':0},
 'GS02':{'idExpansion':'4785','physical':40,'logical':20,'singletons':0},
 'GS03':{'idExpansion':'4766','physical':40,'logical':20,'singletons':0},
 'GS04':{'idExpansion':'4746','physical':40,'logical':20,'singletons':0},
 'GS06':{'idExpansion':'4710','physical':50,'logical':20,'singletons':3},
 'PP19':{'idExpansion':'4650','physical':40,'logical':20,'singletons':0},
}
base.EXPECTED_TOTAL=sum(x['singletons'] for x in base.TARGETS.values())

if __name__=='__main__':
 raise SystemExit(base.main())
