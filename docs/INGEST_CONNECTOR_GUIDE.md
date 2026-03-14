# Ingest connector guide (Don’tRipIt)

## Framework común
Todos los conectores del catálogo (Pokémon, MTG, Yu-Gi-Oh!, Riftbound) siguen este patrón:

1. `load_start` → origen fixture/remote, filtros y limit.
2. `load_progress` → progreso incremental de lotes.
3. `load_done` → total de payloads listos para upsert.
4. `normalize` → mapea a `{set, card, print}` con campos mínimos.
5. `upsert` → set/card/print + id externo + imagen primaria.
6. `run` (base) → checksum dedupe, incremental skip y reindex selectivo por entidades tocadas.

## Campos mínimos por payload normalizado
- `set.code`, `set.name`.
- `card.name`.
- `print.collector_number`, `print.language`, `print.rarity`.
- `print.*_id` externo si existe (scryfall/tcgdex/ygoprodeck/riftbound).
- `print.primary_image_url` cuando la fuente lo provee.

## Matching card / print / set
- **Set**: id externo del proveedor si existe; fallback por `(game_id, code)`.
- **Card**: id externo del proveedor; fallback por `(game_id, name)`.
- **Print**: id externo del proveedor; fallback por identidad funcional
  `(set_id, card_id, collector_number, language, is_foil, variant)`.

## Conector por fuente

### tcgdex_pokemon
- Fuente remota: TCGdex por sets (`/sets`, `/sets/{id}`).
- Limit aplica sobre cartas acumuladas globalmente.
- Soporta `set` filter (set id TCGdex).
- Dedupe estable por `card.id` TCGdex.
- Backfill de `tcgdex_id` para set/card/print en datos legacy.
- Limite conocido: algunos campos de rareza/variantes no vienen completos en endpoint resumido.

### scryfall_mtg
- Fixture o remoto (`bulk default_cards` para full, `/cards/search` para incremental).
- Dedupe por `card.id` (Scryfall print id).
- Upsert con `oracle_id` para agrupar cartas canónicas.
- Guarda `PrintIdentifier(source=scryfall)` e imagen primaria.
- Devuelve ids tocados para reindex selectivo.

### ygoprodeck_yugioh
- Fixture o remoto paginado (`cardinfo.php` con `num/offset`).
- Dedupe estable por `id` de YGOProDeck.
- Normaliza claves canónicas de card/print y evita duplicados inter-set.
- Soporta incremental skip robusto + reparación de prints/imágenes cuando falta hidratar.
- Limite conocido: la API puede rate-limit/403 intermitente, se mitiga con retry exponencial.

### riftbound
- **Modo actual: fixture/manual únicamente** (`riftbound_sample.json` o fixture local equivalente).
- Dedupe por `print.id`.
- Upsert de ids externos `riftbound_id` en set/card/print.
- Guarda `PrintIdentifier(source=riftbound)` e imagen primaria.
- Decisión temporal: se deshabilita ingest remoto hasta contar con endpoint público estable y verificable.

## Dataset mínimo útil recomendado (QA frontend)
- Yu-Gi-Oh!: `limit >= 120`.
- MTG: `limit >= 1000` (bulk o search incremental según entorno).
- Pokémon: ejecutar múltiple set (`--pokemon-all-sets true` o set list), `pokemon_limit` amplio.
- Riftbound: fixture completa o remoto con `limit >= 80` si la fuente lo permite.
