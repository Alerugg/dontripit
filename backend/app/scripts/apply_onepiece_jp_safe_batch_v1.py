from __future__ import annotations

import argparse
import sys

from app.scripts import apply_onepiece_jp_eb_prb_batch_v1 as impl
from app.scripts.onepiece_jp_safe_batches_v1 import BATCHES, CONFIRM_TOKENS, EXPECTED_TOTALS, FULL_SURFACE_SHA256


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--batch", required=True, choices=sorted(BATCHES))
    parsed, remaining = parser.parse_known_args()
    batch = parsed.batch
    impl.EXPECTED = BATCHES[batch]
    impl.EXPECTED_TOTAL = EXPECTED_TOTALS[batch]
    impl.CONFIRM = CONFIRM_TOKENS[batch]
    impl.FULL_SURFACE_SHA256 = FULL_SURFACE_SHA256
    sys.argv = [sys.argv[0], *remaining]
    return impl.main()


if __name__ == "__main__":
    raise SystemExit(main())
