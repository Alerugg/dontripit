from __future__ import annotations

from app.scripts import apply_mtg_v22_snapshot_live as base


# V2.3 is deliberately an identifier-only derivation of the frozen certified
# V2.2 snapshot. All Card/Print/Set counts and loader semantics stay identical;
# only PrintIdentifier now uses the DB-compatible finish-aware namespace.
base.EXPECTED_SCHEMA = "mtg-canonical-v2.3"


def run(*, snapshot_dir, output_path):
    return base.run(snapshot_dir=snapshot_dir, output_path=output_path)


def main() -> None:
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(
        description="Apply certified MTG V2.3 snapshot to live Neon in one transaction"
    )
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(snapshot_dir=args.snapshot_dir, output_path=args.output)


if __name__ == "__main__":
    main()
