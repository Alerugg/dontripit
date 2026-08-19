from __future__ import annotations

import argparse

from app.scripts import reindex_onepiece_jp_eb_prb_batch_v1 as impl
from app.scripts.onepiece_jp_safe_batches_v1 import BATCHES, EXPECTED_TOTALS


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", required=True, choices=sorted(BATCHES))
    args = parser.parse_args()
    impl.TOKENS = set(BATCHES[args.batch])
    impl.EXPECTED = EXPECTED_TOTALS[args.batch]
    return impl.main()


if __name__ == "__main__":
    raise SystemExit(main())
