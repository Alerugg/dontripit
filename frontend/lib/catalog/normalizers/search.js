import { normalizeGameSlug } from '../games.js'

function normalizeDetailLabel(item) {
  const parts = [
    item.set_name || item.set_code || item.code,
    item.collector_number ? `#${item.collector_number}` : null,
    item.language,
    item.finish,
    item.variant,
    item.rarity,
    item.year,
  ].filter(Boolean)

  return parts.join(' · ')
}

function toNumericCount(value, fallback = 0) {
  const count = Number(value)
  return Number.isFinite(count) ? count : fallback
}

function createMasterCard(item, game) {
  return {
    ...item,
    id: item.card_id || item.id,
    card_id: item.card_id || item.id,
    game,
    type: 'card',
    variant_count: toNumericCount(item.variant_count, item.type === 'print' ? 1 : 0),
    variants: [],
    summary_label: item.set_name || item.summary_label || item.set_code || item.code || item.game || 'Carta',
  }
}

export function buildMasterCards(items = []) {
  const masters = new Map()
  const passthrough = []

  items.forEach((rawItem, index) => {
    if (!rawItem || rawItem.type === 'set') {
      if (rawItem) passthrough.push(rawItem)
      return
    }

    const game = normalizeGameSlug(rawItem.game || '')

    if (rawItem.type === 'card') {
      const master = createMasterCard(rawItem, game)
      masters.set(`${game || 'game'}:${master.card_id}`, master)
      return
    }

    if (!rawItem.card_id) {
      passthrough.push({
        ...rawItem,
        game,
        variant_count: toNumericCount(rawItem.variant_count, 1),
        variant_label: normalizeDetailLabel(rawItem),
        _sort_index: index,
      })
      return
    }

    const masterKey = `${game || 'game'}:${rawItem.card_id}`
    const variantId = rawItem.id || rawItem.print_id || `${masterKey}:${index}`
    const nextVariant = {
      ...rawItem,
      game,
      type: 'print',
      variant_label: normalizeDetailLabel(rawItem),
    }

    if (!masters.has(masterKey)) {
      masters.set(masterKey, createMasterCard(rawItem, game))
    }

    const master = masters.get(masterKey)
    const alreadyPresent = master.variants.some((variant) => String(variant.id) === String(variantId))
    if (!alreadyPresent) {
      master.variants.push({ ...nextVariant, id: variantId })
    }

    if (!master.primary_image_url && rawItem.primary_image_url) {
      master.primary_image_url = rawItem.primary_image_url
    }

    if (!master.set_name && rawItem.set_name) {
      master.set_name = rawItem.set_name
    }

    if (!master.language && rawItem.language) {
      master.language = rawItem.language
    }

    master.variant_count = Math.max(
      toNumericCount(master.variant_count),
      toNumericCount(rawItem.variant_count, 0),
      master.variants.length,
    )
  })

  return [
    ...Array.from(masters.values()).map((master) => ({
      ...master,
      variant_count: Math.max(toNumericCount(master.variant_count), master.variants.length),
      variants: master.variants,
    })),
    ...passthrough,
  ]
}
