# Catalog Health — After One Piece Bootstrap

Date: 2026-08-07
Branch: `catalog-v2`
Neon environment: production catalog database
Status: **One Piece bootstrap successful; not yet Catalog Certified**

## Executive summary

The first canonical One Piece bootstrap completed successfully using the optimized empty-catalog batch strategy.

The previous row-by-row bootstrap was cancelled before commit after a read-only `pg_stat_activity` probe showed an N+1 bottleneck on `print_identifiers`. Its transaction rolled back cleanly and left the canonical catalog unchanged.

The optimized bootstrap moved remote download and normalization outside the database transaction, then inserted sets/cards/prints/images/identifiers in batches. A read-only Neon activity probe during the remote phase showed zero active DB sessions, confirming that no long idle transaction was held open.

## Source result

Official fallback source was used because the historical PunkRecords repository returned HTTP 404.

Source payload:

- Sets: 56
- Cards: 1,121
- Payload checksum: `54ae8c03083e9799f0ed646d4202207b40e1db6af4313ac0f3b6626ab153ebb1`

Prepared canonical payload:

- Sets: 56
- Cards: 1,121
- Prints: 3,810

Inserted:

- Sets: 56
- Cards: 1,121
- Prints: 3,810
- Primary images: 3,810
- Structured external identifiers: 3,810

The old stale cancelled One Piece `IngestRun` was closed after the successful bootstrap.

## One Piece Catalog Health

Status reported by internal Catalog Health: **healthy**

Counts:

- Sets: 56
- Cards: 1,121
- Prints: 3,810
- Images: 3,810
- Prints with any image: 3,810
- Prints with primary image: 3,810
- Prints with structured identifier: 3,810
- Prints with any external identifier: 3,810

Internal integrity issues:

- Sets without prints: 0
- Cards without prints: 0
- Cards missing `card_key`: 0
- Prints missing language: 0
- Prints missing rarity: 0
- Prints missing `print_key`: 0
- Prints without image: 0
- Prints without primary image: 0
- Prints without external identifier: 0
- Potential duplicate print identity groups: 0
- Sets missing release date: **56**

Languages:

- `en`: 3,810 prints

Variants:

- `default`: 2,541
- `parallel`: 885
- `r1`: 352
- `r2`: 32

Rarities:

- `C`: 1,407
- `R`: 795
- `UC`: 676
- `SR`: 516
- `L`: 249
- `SEC`: 87
- `SP CARD`: 71
- `TR`: 9

## Catalog totals after bootstrap

- Games: 5
- Sets: 5,826
- Cards: 4,579
- Prints: 12,119
- Images: 12,125

Search index rebuild succeeded after the canonical commit:

- Cards indexed: 4,579
- Sets indexed: 5,826
- Prints indexed: 12,119

## Important interpretation

`healthy` currently means the records stored in the canonical database are internally consistent according to Catalog Health rules. It does **not** mean the One Piece catalog has been proven externally complete.

Before declaring One Piece `Catalog Certified`, the official source parser must be audited for coverage of:

- promo collector numbers such as `P-xxx`;
- premium/reprint products such as PRB and other series where the commercial product identity may differ from the card's original collector-number prefix;
- tournament/event/promotional releases;
- exact print ownership when the same collector number is reprinted in a later product;
- release dates for canonical sets/products;
- card identity where multiple mechanically distinct cards share the same visible name.

## Next gate

Do not move the product roadmap to pricing on the basis of this internal-health result alone.

Next gate: **One Piece external source coverage audit → identity corrections → Catalog Certified criteria.**
