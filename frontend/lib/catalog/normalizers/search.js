import { normalizeGameSlug } from '../games'

function pickMasterKey(item) {
  return item.card_id || item.cardId || item.printitall_card_code || item.oracle_id || item.id || item.name
}

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

export function buildMasterCards(items = []) {
  const masters = new Map()

  items.forEach((rawItem) => {
    if (!rawItem || rawItem.type === 'set') return

    const game = normalizeGameSlug(rawItem.game || '')
    const masterKey = `${game || 'game'}:${pickMasterKey(rawItem)}`
    const variantId = rawItem.id || rawItem.print_id || `${masterKey}:${masters.size}`
    const nextVariant = {
      ...rawItem,
      game,
      type: rawItem.type || 'card',
      variant_label: normalizeDetailLabel(rawItem),
    }

    if (!masters.has(masterKey)) {
      masters.set(masterKey, {
        ...rawItem,
        id: rawItem.card_id || rawItem.id,
        card_id: rawItem.card_id || rawItem.id,
        game,
        type: 'card',
        primary_image_url: rawItem.primary_image_url,
        variant_count: 0,
        variants: [],
        summary_label: rawItem.set_name || rawItem.set_code || rawItem.code || rawItem.game || 'Carta',
      })
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
  })

  return Array.from(masters.values()).map((master) => ({
    ...master,
    variant_count: master.variants.length,
    variants: master.variants,
  }))
}
