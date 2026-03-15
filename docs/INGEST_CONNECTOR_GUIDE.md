# Ingest connector guide (Don’tRipIt)

## Framework común
Todos los conectores del catálogo (Pokémon, MTG, Yu-Gi-Oh!, Riftbound, One Piece) siguen este patrón:

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
- Modo configurable por `RIFTBOUND_SOURCE=official|fallback|auto`.
- `auto` prioriza backend oficial (Riot `riftbound-content-v1`) cuando hay `RIFTBOUND_API_BASE_URL` + `RIFTBOUND_API_KEY`; si faltan, usa fallback.
- Backend fallback soporta fixture local (`riftbound_sample.json`) y remoto opcional (`RIFTBOUND_FALLBACK_BASE_URL`).
- El backend oficial consume `GET /riftbound/content/v1/contents` (catálogo unificado por set con cards anidadas), autentica con `X-Riot-Token`, y transforma a `{set, card, print}` sin asumir endpoints `/sets|/cards|/prints`.
- Ambos backends mapean al mismo esquema lógico y comparten normalización `{set, card, print}`.
- Dedupe por checksum + `riftbound_id` y fallback funcional `(set_id, card_id, collector_number, language, variant)`.
- Guarda `PrintIdentifier(source=riftbound)`; usa imagen oficial (`art.fullURL` / `art.thumbnailURL`) cuando existe y cae a placeholder local solo cuando no hay asset usable.

### onepiece
- Modo configurable por `ONEPIECE_SOURCE=fixture|remote`.
- `fixture` conserva `onepiece_punkrecords_sample.json` para tests/local reproducible.
- `remote` consume Punk Records por dos endpoints (`sets` + `cards`) configurables vía env (`ONEPIECE_PUNKRECORDS_*`).
- Soporta auth opcional con `GITHUB_TOKEN` para `api.github.com`; recomendado para evitar rate limits (403/429) en ingest remoto.
- La imagen primaria prioriza `img_full_url`; fallback ordenado: `image_url` → `img_url` → `img_thumb_url` → `image`.
- Si no hay URL válida, usa `ONEPIECE_IMAGE_FALLBACK_URL` (controlado; sin URLs fake `example.cdn.onepiece` en remoto).
- Dedupe/idempotencia mantiene `print_key` (`onepiece:{set}:{collector_norm}:{lang}:{variant}`) + checksum incremental.

## Dataset mínimo útil recomendado (QA frontend)
- Yu-Gi-Oh!: `limit >= 120`.
- MTG: `limit >= 1000` (bulk o search incremental según entorno).
- Pokémon: ejecutar múltiple set (`--pokemon-all-sets true` o set list), `pokemon_limit` amplio.
- Riftbound: fixture completa o remoto con `limit >= 80` si la fuente lo permite.
