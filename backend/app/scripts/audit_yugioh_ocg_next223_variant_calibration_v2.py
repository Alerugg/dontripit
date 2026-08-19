from __future__ import annotations

import os
from app.scripts import audit_yugioh_ocg_variant_ordinal_calibration_v1 as cal
from app.scripts.apply_yugioh_ocg_next_singleton_heavy_cohort223_v2 import base

cal.TARGETS=base.TARGETS

if __name__=='__main__':
 os.environ.setdefault('YGO_OCG_VARIANT_ORDINAL_CALIBRATION_OUTPUT','/tmp/ygo-ocg-next223-variant-calibration-v2.json')
 raise SystemExit(cal.main())
