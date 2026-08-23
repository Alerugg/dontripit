from __future__ import annotations

"""Performance-only runner for V4 DON geometry calibration.

The underlying fingerprints, crops, ranking and output are unchanged. Hamming
距離 is computed directly as XOR + int.bit_count() on each 256-bit hex hash,
which is mathematically identical to ImageHash subtraction but avoids millions
of repeated object conversions.
"""

from app.scripts import audit_onepiece_don_image_crosswalk_v4 as v4


def _distance(left: dict, right: dict) -> dict:
    components = {
        key: (int(left[key], 16) ^ int(right[key], 16)).bit_count()
        for key in v4.HASH_KEYS
    }
    ordered = sorted(components.values())
    return {
        **components,
        "sum": int(sum(components.values())),
        "max": int(max(components.values())),
        "sum_best3": int(sum(ordered[:3])),
    }


v4._distance = _distance


if __name__ == "__main__":
    raise SystemExit(v4.main())
