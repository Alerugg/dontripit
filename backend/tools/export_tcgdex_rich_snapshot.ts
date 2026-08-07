import { createWriteStream } from 'node:fs'
import { basename, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { once } from 'node:events'

const POCKET_SERIES = 'Pokémon TCG Pocket'

function arg(name: string): string {
  const index = Bun.argv.indexOf(name)
  if (index < 0 || !Bun.argv[index + 1]) throw new Error(`Missing required ${name}`)
  return Bun.argv[index + 1]
}

function english(value: any): string | null {
  if (typeof value === 'string') return value
  if (value && typeof value === 'object' && typeof value.en === 'string') return value.en
  return null
}

function cleanObject(value: any): any {
  if (Array.isArray(value)) return value.map(cleanObject)
  if (!value || typeof value !== 'object') return value
  const out: Record<string, any> = {}
  for (const [key, child] of Object.entries(value)) {
    if (child === undefined) continue
    out[key] = cleanObject(child)
  }
  return out
}

function compactAbility(row: any) {
  return {
    type: row?.type ?? null,
    name: english(row?.name),
    effect: english(row?.effect),
  }
}

function compactAttack(row: any) {
  return {
    cost: Array.isArray(row?.cost) ? row.cost : [],
    name: english(row?.name),
    effect: english(row?.effect),
    damage: row?.damage ?? null,
  }
}

function compactLocalizedItem(row: any) {
  if (!row) return null
  return { name: english(row.name), effect: english(row.effect) }
}

async function writeLine(stream: ReturnType<typeof createWriteStream>, payload: any) {
  if (!stream.write(`${JSON.stringify(payload)}\n`)) await once(stream, 'drain')
}

async function main() {
  const sourceRoot = resolve(arg('--source-root'))
  const outputPath = resolve(arg('--output'))
  const manifestPath = resolve(arg('--manifest'))
  const sourceVersion = arg('--source-version')

  const glob = new Bun.Glob('data/**/*.ts')
  const files: string[] = []
  for await (const file of glob.scan({ cwd: sourceRoot, onlyFiles: true })) {
    // Card modules live below data/<series>/<set>/<localId>.ts. Set and series
    // metadata files are shallower and are intentionally excluded.
    if (file.split('/').length >= 4) files.push(file)
  }
  files.sort((a, b) => a.localeCompare(b, 'en'))

  const stream = createWriteStream(outputPath, { encoding: 'utf8' })
  const ids = new Set<string>()
  const errors: Array<{ file: string, error: string }> = []
  const duplicateIds: string[] = []
  const coverage: Record<string, number> = {}
  const categoryCounts: Record<string, number> = {}
  const rarityCounts: Record<string, number> = {}
  const stageCounts: Record<string, number> = {}
  const variantShapes: Record<string, number> = {}
  let physicalCards = 0
  let pocketCards = 0
  let nonCardModules = 0

  const bump = (map: Record<string, number>, key: any) => {
    const clean = String(key ?? 'missing')
    map[clean] = (map[clean] || 0) + 1
  }
  const cover = (key: string, value: any) => {
    const present = Array.isArray(value) ? value.length > 0 : value !== null && value !== undefined && value !== ''
    if (present) coverage[key] = (coverage[key] || 0) + 1
  }

  for (const file of files) {
    try {
      const moduleUrl = pathToFileURL(resolve(sourceRoot, file)).href
      const imported = await import(moduleUrl)
      const card = imported?.default
      if (!card || typeof card !== 'object' || !card.set?.id || !card.name) {
        nonCardModules += 1
        continue
      }

      const localId = basename(file, '.ts')
      const sourceId = `${card.set.id}-${localId}`
      const seriesName = english(card.set?.serie?.name)
      if (seriesName === POCKET_SERIES) {
        pocketCards += 1
        continue
      }

      if (ids.has(sourceId)) duplicateIds.push(sourceId)
      ids.add(sourceId)
      physicalCards += 1

      const variants = card.variants === undefined ? null : cleanObject(card.variants)
      const variantShape = Array.isArray(variants)
        ? 'detailed_array'
        : variants && typeof variants === 'object'
          ? 'legacy_flags'
          : 'missing'

      const attributes = {
        category: card.category ?? null,
        rarity: card.rarity ?? null,
        illustrator: card.illustrator ?? null,
        regulation_mark: card.regulationMark ?? null,
        dex_id: Array.isArray(card.dexId) ? card.dexId : [],
        hp: card.hp ?? null,
        types: Array.isArray(card.types) ? card.types : [],
        evolve_from: english(card.evolveFrom),
        weight: card.weight ?? null,
        description: english(card.description),
        level: card.level ?? null,
        stage: card.stage ?? null,
        suffix: card.suffix ?? null,
        held_item: compactLocalizedItem(card.item),
        abilities: Array.isArray(card.abilities) ? card.abilities.map(compactAbility) : [],
        attacks: Array.isArray(card.attacks) ? card.attacks.map(compactAttack) : [],
        weaknesses: Array.isArray(card.weaknesses) ? cleanObject(card.weaknesses) : [],
        resistances: Array.isArray(card.resistances) ? cleanObject(card.resistances) : [],
        retreat: card.retreat ?? null,
        effect: english(card.effect),
        trainer_type: card.trainerType ?? null,
        energy_type: card.energyType ?? null,
        boosters: Array.isArray(card.boosters) ? card.boosters : [],
        variants,
        variant_shape: variantShape,
        third_party: card.thirdParty ? cleanObject(card.thirdParty) : null,
      }

      cover('rarity', attributes.rarity)
      cover('illustrator', attributes.illustrator)
      cover('regulation_mark', attributes.regulation_mark)
      cover('hp', attributes.hp)
      cover('types', attributes.types)
      cover('stage', attributes.stage)
      cover('variants', attributes.variants)
      bump(categoryCounts, attributes.category)
      bump(rarityCounts, attributes.rarity)
      bump(stageCounts, attributes.stage)
      bump(variantShapes, variantShape)

      await writeLine(stream, {
        source_id: sourceId,
        local_id: localId,
        name: english(card.name),
        set: {
          id: card.set.id,
          name: english(card.set.name),
          series_id: card.set?.serie?.id ?? null,
          series_name: seriesName,
          release_date: typeof card.set.releaseDate === 'string' ? card.set.releaseDate : english(card.set.releaseDate),
        },
        attributes,
        source_file: file,
        source_version: sourceVersion,
      })
    } catch (error: any) {
      errors.push({ file, error: String(error?.stack || error) })
    }
  }

  stream.end()
  await once(stream, 'finish')

  const manifest = {
    generated_at: new Date().toISOString(),
    source_repository: 'tcgdex/cards-database',
    source_version: sourceVersion,
    scanned_ts_files: files.length,
    physical_cards: physicalCards,
    pocket_cards_excluded: pocketCards,
    non_card_modules: nonCardModules,
    unique_source_ids: ids.size,
    duplicate_source_ids: [...new Set(duplicateIds)].sort(),
    import_errors: errors,
    coverage,
    category_counts: categoryCounts,
    rarity_counts: rarityCounts,
    stage_counts: stageCounts,
    variant_shapes: variantShapes,
    status: errors.length === 0 && duplicateIds.length === 0 ? 'pass' : 'fail',
  }
  await Bun.write(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`)
  console.log(JSON.stringify(manifest, null, 2))

  if (errors.length) throw new Error(`Snapshot export had ${errors.length} module import errors`)
  if (duplicateIds.length) throw new Error(`Snapshot export had ${duplicateIds.length} duplicate source IDs`)
}

await main()
