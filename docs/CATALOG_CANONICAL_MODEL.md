# CATALOG_CANONICAL_MODEL

## Scope (ETAPA 1)
Este documento define el **modelo canónico unificado del catálogo TCG** para API-PROJECT, basado en auditoría del estado actual de:
- esquema SQLAlchemy + migraciones,
- pipeline de ingest,
- conectores MTG/Pokémon/Yu-Gi-Oh!/Riftbound,
- rutas de catálogo/search que consumen el modelo.

Objetivo: estabilizar identidad/dedupe/trazabilidad antes de ampliar conectores o ingest masivo.

---

## 1) Entidades canónicas

### `game`
Representa un TCG (MTG, Pokémon, Yu-Gi-Oh!, Riftbound).

**Canónico (mínimo):**
- `id` (pk)
- `slug` (estable, único global)
- `name`

**Regla:** cualquier identidad de catálogo es siempre *namespaced por game* salvo IDs de fuente que sean globales por definición del proveedor.

---

### `set`
Conjunto/editorial dentro de un `game`.

**Canónico (mínimo):**
- `id` (pk)
- `game_id` (fk)
- `code` (código canónico interno del set)
- `name`
- `release_date` (opcional)

**No canónico (pero útil):**
- aliases de código/nombre por fuente
- metadata editorial no usada en identidad

---

### `card`
Entidad intelectual/base (la “carta” independiente de edición concreta).

**Canónico (mínimo):**
- `id` (pk)
- `game_id` (fk)
- `canonical_name` (ver estrategia de prioridad)

**Canónico recomendado (futuro):**
- `card_key` (string estable interno generado)
- `name_normalized` (búsqueda/dedupe)

**Importante:** `card` no debe depender de precio ni de datos comerciales.

---

### `print`
Impresión específica de una carta en un set (coleccionable físico/digital según juego).

**Canónico (mínimo):**
- `id` (pk)
- `card_id` (fk)
- `set_id` (fk)
- `collector_number` (normalizado)
- `language`
- `finish` (`nonfoil`, `foil`, etc.; hoy modelado parcialmente por `is_foil`)
- `variant` (layout/tratamiento/rareza especial)
- `rarity` (informativo; **no gobierna identidad** en todos los juegos)

**Canónico recomendado (futuro):**
- `print_key` (string estable interno)

---

### `variant`
Dimensión explícita para diferencias de impresión que no cambian la entidad `card`.

**Problema actual:** se usa `variant` como string libre en `prints`, pero semánticamente mezcla rareza/acabado/promos.

**Canónico recomendado:**
- `print_variant` estructurado (o tabla dedicada) con:
  - `variant_type` (ej. `finish`, `art`, `frame`, `promo_mark`, `edition`)
  - `variant_value`
- mantener columna derivada `variant_key` para unicidad.

---

### `image`
Recurso visual asociado a `print` (y opcionalmente `card` en fallback).

**Canónico (mínimo):**
- `id`
- `print_id`
- `url`
- `is_primary`
- `source`

**Canónico recomendado:**
- `image_type` (`small`, `normal`, `large`, `scan`, etc.)
- `checksum`/`content_hash` opcional para dedupe real
- `priority` explícita por fuente

---

### `external_id`
Mapeo de identidad externa por fuente.

**Canónico recomendado (único modelo para game/set/card/print):**
- `entity_type` (`game|set|card|print|product_variant`)
- `entity_id`
- `source`
- `id_type` (ej. `scryfall_id`, `oracle_id`, `tcgdex_id`, `set_code`, etc.)
- `external_id`
- `is_primary`

Esto evita proliferación de columnas por proveedor en tablas core.

---

### `source`
Proveedor/conector de ingest.

**Canónico (mínimo):**
- `id`
- `name`
- `description`

---

### `ingest_run`
Ejecución auditable de ingest por `source`.

**Canónico (mínimo):**
- `id`
- `source_id`
- `started_at`, `finished_at`
- `status`
- `counts_json`
- `error_summary`

**Recomendado:** añadir `cursor_before`, `cursor_after`, `options_json` (set/lang/limit/incremental).

---

### `source_raw_object` (recomendado mantener/expandir)
Persistencia de payload raw por fuente.

El sistema ya tiene `source_records` (checksum + raw_json), pero hoy no enlaza claramente cada raw object con entidad canónica afectada.

**Canónico recomendado:**
- conservar `source_records`
- extender con:
  - `source_object_id` (id nativo de la fuente)
  - `entity_type`/`entity_id` resueltos (nullable al inicio)
  - `ingest_run_id`
  - `first_seen_at` / `last_seen_at`

Esto mejora trazabilidad, replay y debugging de dedupe.

---

## 2) Relaciones canónicas

- `game 1 --- n set`
- `game 1 --- n card`
- `set 1 --- n print`
- `card 1 --- n print`
- `print 1 --- n image`
- `entity 1 --- n external_id` (para `set/card/print`)
- `source 1 --- n ingest_run`
- `source 1 --- n source_raw_object`

**Restricción funcional clave:** `print.game_id` se deriva por integridad desde `set -> game` y `card -> game`; ambos deben coincidir.

---

## 3) Identidad única (núcleo anti-duplicados)

### 3.1 Identidad de `card`

#### Base común propuesta
`card` es única por:
- `(game_id, canonical_card_key)`

Donde `canonical_card_key` se obtiene por prioridad:
1. ID canónico del juego si existe (p.ej. MTG `oracle_id`),
2. si no, mapping fuerte de external IDs confiables,
3. fallback controlado por nombre normalizado + señal de colisión.

#### Política por juego (pragmática)
- **MTG:** identidad fuerte por `oracle_id`.
- **Pokémon / Yu-Gi-Oh! / Riftbound:** mientras no exista equivalente universal, usar `card_key` interno + `external_id` primario por fuente, y no solo nombre.

**Riesgo actual:** unicidad por `(game_id, name)` causa merges falsos y/o bloqueos cuando hay homónimos reales.

---

### 3.2 Identidad de `print`

#### Base común propuesta
`print` única por:
- `(card_id, set_id, collector_number_norm, language_norm, finish_norm, variant_key)`

**Notas:**
- `rarity` fuera de la clave.
- `collector_number` debe normalizarse (`001`, `1`, `1a` reglas por juego).
- `finish_norm` reemplaza ambigüedad de `is_foil` + parte de `variant`.

#### Excepciones por juego
- Si proveedor ofrece print-id global estable (Scryfall print id, tcgdex card/print id, etc.), se usa como matching prioritario, pero la clave canónica sigue existiendo como red de seguridad.

---

### 3.3 Unicidad de `external source`

Para tabla unificada `external_id`:
- `UNIQUE(source, id_type, external_id)`  (un external id no puede apuntar a dos entidades)
- `UNIQUE(entity_type, entity_id, source, id_type)`

Para modelo actual (mientras migra):
- mantener UQ por columnas de IDs externas en tablas.
- corregir `print_identifiers` para no limitar a un solo identifier por fuente y print.

---

## 4) Campos canónicos vs campos de fuente

### Campos canónicos (gobiernan catálogo)
- game: `slug`, `name`
- set: `game_id`, `code`, `name`, `release_date`
- card: `game_id`, `canonical_name` (y `card_key` recomendado)
- print: `card_id`, `set_id`, `collector_number_norm`, `language_norm`, `finish_norm`, `variant_key`
- image: `print_id`, `url`, `is_primary`, `source`

### Campos de fuente (no gobiernan identidad canónica por sí solos)
- strings de rareza de proveedor (`set_rarity`, etc.)
- URLs auxiliares o tamaños específicos
- metadata de presentación

### External metadata (guardar, pero separada)
- payload completo raw por fuente
- valores no homologados por juego
- fields conflictivos entre proveedores

---

## 5) Prioridad de fuentes y resolución de conflictos

### Estrategia recomendada
Definir policy por `game` y `field`:
- `field_priority(game, field) = [source_a, source_b, ...]`

Ejemplo inicial:
- **MTG**
  - nombre carta: `scryfall` > otras
  - imágenes: `scryfall`
  - collector_number: `scryfall`
- **Pokémon**
  - nombre/set/card id: `tcgdex` > fixture_local
  - imágenes: `tcgdex`
- **Yu-Gi-Oh!**
  - nombre/prints: `ygoprodeck`
- **Riftbound**
  - fuente actual única (fixture/connector propio)

### Cuando discrepan 2 fuentes
1. Mantener valor canónico según prioridad.
2. Guardar valor alternativo en `field_provenance`/metadata.
3. Registrar conflicto auditable (contador + detalle).

### Trazabilidad
- Cada write de campo canónico debe dejar rastro con: `entity`, `field`, `source`, `value`, `timestamp`, `ingest_run_id`.

---

## 6) Contrato `normalized payload` (estándar para conectores)

Todos los conectores deben emitir un contrato homogéneo antes de `upsert`:

```json
{
  "normalized_game": {
    "slug": "pokemon",
    "name": "Pokémon"
  },
  "normalized_set": {
    "source_key": "sv1",
    "code": "sv1",
    "name": "Scarlet & Violet",
    "release_date": "2023-03-31",
    "external_ids": [{"source": "tcgdex", "id_type": "set_id", "value": "sv1"}],
    "raw": {}
  },
  "normalized_card": {
    "source_key": "xy7-54",
    "canonical_name": "Pikachu",
    "name_normalized": "pikachu",
    "identity_hints": {"oracle_id": null},
    "external_ids": [{"source": "tcgdex", "id_type": "card_id", "value": "xy7-54"}],
    "raw": {}
  },
  "normalized_prints": [
    {
      "source_key": "xy7-54",
      "collector_number": "54",
      "collector_number_norm": "54",
      "language": "en",
      "finish": "nonfoil",
      "variant_key": "default",
      "rarity": "common",
      "external_ids": [{"source": "tcgdex", "id_type": "print_id", "value": "xy7-54"}],
      "raw": {}
    }
  ],
  "normalized_images": [
    {
      "print_source_key": "xy7-54",
      "url": "https://...",
      "is_primary": true,
      "source": "tcgdex",
      "image_type": "high"
    }
  ],
  "normalized_external_ids": []
}
```

Reglas:
- `normalize()` no escribe DB.
- `upsert()` solo consume el contrato normalizado.
- cada conector debe generar claves estables (`source_key`) para linkear set/card/print/images dentro del lote.

---

## 7) Auditoría del estado actual

## 7.1 Qué ya coincide
- Existe separación base `games/sets/cards/prints/print_images`.
- Existen `sources`, `source_records`, `source_sync_state`, `ingest_runs`.
- Hay soporte inicial de provenance (`field_provenance`).
- Conectores principales hacen normalize + upsert con patrón similar.
- Hay `variant` en `prints` y UQ ampliada con variant.

## 7.2 Gaps vs modelo canónico deseado

1. **Identidad de card frágil**
   - `cards` mantiene `UNIQUE(game_id, name)`: no permite homónimos legítimos y empuja merges por nombre.

2. **External IDs acoplados al schema core**
   - columnas específicas por proveedor (`tcgdex_id`, `yugioh_id`, etc.) en varias tablas.
   - escalar a nuevas fuentes implica más migraciones/columnas.

3. **`print_identifiers` inconsistente**
   - UQ actual `UNIQUE(print_id, source)` limita múltiples id_types por misma fuente.
   - además no hay UQ global `source+external_id` en modelo actual ORM.

4. **`variant` semánticamente ambiguo**
   - mezcla acabados/rareza y otros conceptos sin taxonomía común.

5. **Normalización heterogénea por conector**
   - cada conector devuelve estructuras distintas (`set/card`, `sets/prints`, etc.).
   - el engine no valida un contrato común.

6. **Trazabilidad parcial**
   - `source_records` guarda raw+checksum pero sin enlace fuerte a entidades afectadas ni a `ingest_run`.

7. **Riesgo de mezcla catálogo/comercial**
   - mismo pipeline `fixture_local` procesa catálogo y también precios/productos.
   - aumenta acoplamiento y riesgo operativo.

8. **Reindex search full por run**
   - `rebuild_search_documents(session)` se llama completo en cada `run`; no incremental por touched ids.

## 7.3 Zonas frágiles/riesgo de duplicados
- Dedupe fallback por nombre en card (sobre todo no-MTG).
- Matching de print depende de combinación variable por conector.
- `collector_number` sin normalización transversal.
- idioma por defecto impuesto (`en`) en varios casos puede colisionar con datos multi-language reales.
- ingest incremental depende de checksum raw; cambios de forma/noise pueden reingestar sin cambios semánticos.

---

## 8) Recommended next changes (priorizadas)

### P0 — seguridad de identidad (sin cambios destructivos)
1. Definir utilidades comunes de normalización:
   - `normalize_collector_number(game, raw)`
   - `normalize_language(raw)`
   - `normalize_finish(is_foil, variant, source_payload)`
2. Definir contrato único `normalized_*` y validarlo en runtime (schema ligero Python).
3. Introducir `card_key` y `print_key` internos (nullable al principio), rellenables en backfill.
4. Ajustar `print_identifiers` a modelo con `id_type` y unicidades correctas.

### P1 — trazabilidad y auditoría
5. Extender `source_records` con `ingest_run_id` y campos de vinculación (`source_object_id`, `entity_type/id`).
6. Añadir logging estructurado de conflictos por campo (source priority + evidencia).

### P2 — evolución de schema canónico
7. Crear tabla unificada `external_ids` para entidades de catálogo.
8. Mantener columnas legacy por transición (lectura dual), deprecarlas por fases.
9. Añadir tabla/estructura `print_variants` o `variant_dimensions`.

### P3 — pipeline y dependencias
10. Separar claramente ingest de catálogo vs ingest comercial/precios (pipelines distintos, shared libs comunes).
11. Reindex search incremental usando touched ids del run.
12. Añadir tests de identidad canónica por juego (homónimos, reprints, multi-finish, multi-source merge).

---

## 9) Plan de migración recomendado (no destructivo)

1. **Fase A (aditiva):** nuevas tablas/campos + backfill + dual-write.
2. **Fase B (verificación):** jobs de auditoría comparando identidad legacy vs canónica.
3. **Fase C (switch):** lecturas del catálogo pasan a claves canónicas.
4. **Fase D (cleanup):** deprecación gradual de columnas por fuente en core.

---

## 10) Decisiones de arquitectura (resumen ejecutivo)

1. La identidad canónica no puede depender solo de `name` ni de un único proveedor.
2. `card` y `print` deben tener claves internas estables (`card_key`, `print_key`).
3. Los IDs externos deben unificarse en un modelo extensible (`external_ids`).
4. El contrato de `normalize()` debe ser único para todos los conectores.
5. Trazabilidad de origen debe ser first-class (`source_raw_object` vinculado a run y entidad).
6. Catálogo y comercial/precios deben desacoplarse operativamente.

## Implementation notes (P0)

- Se introdujo un contrato runtime para `normalized payload` en `backend/app/ingest/normalized_schema.py` y validación central en `SourceConnector.validate_payload_contract`.
- Se añadieron utilidades compartidas de canonicalización en `backend/app/ingest/normalization.py`, incluyendo generación determinística de `card_key` y `print_key`.
- Se agregaron columnas `cards.card_key` y `prints.print_key` con índices; `prints.print_key` quedó con `UNIQUE` nullable para transición segura.
- El conector piloto `ygoprodeck_yugioh` ahora emite contrato normalizado y mantiene llaves legacy (`card/sets/prints`) temporalmente para compatibilidad de transición.
