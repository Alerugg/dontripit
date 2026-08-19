from __future__ import annotations

FULL_SURFACE_SHA256 = "772684372981c8004acc0b17598f2853118b2ea0c375e5654631b2cfacdf2008"

BATCHES = {
    "op01_05": {
        "OP01": {"physical": 249, "logical": 121},
        "OP02": {"physical": 240, "logical": 121},
        "OP03": {"physical": 246, "logical": 123},
        "OP04": {"physical": 202, "logical": 119},
        "OP05": {"physical": 268, "logical": 119},
    },
    "op06_10": {
        "OP06": {"physical": 269, "logical": 119},
        "OP07": {"physical": 225, "logical": 119},
        "OP08": {"physical": 180, "logical": 119},
        "OP09": {"physical": 266, "logical": 119},
        "OP10": {"physical": 190, "logical": 119},
    },
    "op11_15": {
        "OP11": {"physical": 167, "logical": 119},
        "OP12": {"physical": 184, "logical": 119},
        "OP13": {"physical": 194, "logical": 120},
        "OP14": {"physical": 163, "logical": 120},
        "OP15": {"physical": 152, "logical": 119},
    },
    "st01_12": {
        "ST01": {"physical": 84, "logical": 17},
        "ST02": {"physical": 47, "logical": 17},
        "ST03": {"physical": 47, "logical": 17},
        "ST04": {"physical": 37, "logical": 17},
        "ST05": {"physical": 37, "logical": 17},
        "ST06": {"physical": 29, "logical": 17},
        "ST07": {"physical": 26, "logical": 17},
        "ST08": {"physical": 16, "logical": 15},
        "ST09": {"physical": 19, "logical": 15},
        "ST10": {"physical": 33, "logical": 17},
        "ST11": {"physical": 12, "logical": 5},
        "ST12": {"physical": 26, "logical": 17},
    },
    "st13_24": {
        "ST13": {"physical": 49, "logical": 19},
        "ST14": {"physical": 29, "logical": 17},
        "ST15": {"physical": 9, "logical": 5},
        "ST16": {"physical": 14, "logical": 5},
        "ST17": {"physical": 16, "logical": 5},
        "ST18": {"physical": 16, "logical": 5},
        "ST19": {"physical": 8, "logical": 5},
        "ST20": {"physical": 7, "logical": 5},
        "ST21": {"physical": 39, "logical": 17},
        "ST22": {"physical": 31, "logical": 17},
        "ST23": {"physical": 7, "logical": 5},
        "ST24": {"physical": 6, "logical": 5},
    },
    "st25_36": {
        "ST25": {"physical": 5, "logical": 5},
        "ST26": {"physical": 6, "logical": 5},
        "ST27": {"physical": 5, "logical": 5},
        "ST28": {"physical": 5, "logical": 5},
        "ST29": {"physical": 32, "logical": 17},
        "ST30": {"physical": 34, "logical": 17},
        "ST31": {"physical": 5, "logical": 5},
        "ST32": {"physical": 5, "logical": 5},
        "ST33": {"physical": 5, "logical": 5},
        "ST34": {"physical": 5, "logical": 5},
        "ST35": {"physical": 5, "logical": 5},
        "ST36": {"physical": 5, "logical": 5},
    },
}

EXPECTED_TOTALS = {
    batch: sum(row["physical"] for row in sets.values())
    for batch, sets in BATCHES.items()
}

CONFIRM_TOKENS = {
    batch: f"APPLY_ONEPIECE_JP_SAFE_BATCH_{batch.upper()}"
    for batch in BATCHES
}

# READ ONLY post-production retrigger for ST25-36; no data or guard changes.
