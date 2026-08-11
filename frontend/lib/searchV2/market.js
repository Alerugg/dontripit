export function safeCardmarketUrl(websitePath) {
  const raw = String(websitePath || '').trim()
  if (!raw) return null
  if (raw.startsWith('/')) return `https://www.cardmarket.com${raw}`
  try {
    const parsed = new URL(raw)
    if (parsed.protocol === 'https:' && ['cardmarket.com', 'www.cardmarket.com'].includes(parsed.hostname.toLowerCase())) return raw
  } catch {
    return null
  }
  return null
}

function positive(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

export function marketFromSearchItem(item = {}) {
  const idProduct = String(item.cardmarket_id_product || '').trim()
  if (!idProduct) return null

  const priceValue = positive(item.cardmarket_price)
  const websitePath = item.cardmarket_website_path || null
  const price = priceValue === null ? null : {
    value: priceValue,
    conservative: priceValue,
    currency: item.cardmarket_currency || 'EUR',
    source: 'cardmarket',
    as_of: item.cardmarket_as_of || null,
  }

  return {
    print_id: item.print_id || item.id || null,
    status: price ? 'priced' : 'unpriced',
    reason: price ? null : 'no_current_cardmarket_priceguide_row',
    reference: {
      external_product_id: item.cardmarket_external_product_id || null,
      id_product: idProduct,
      product_name: item.cardmarket_product_name || null,
      provider: 'cardmarket',
      website_path: websitePath,
      url: safeCardmarketUrl(websitePath),
      mapping_confidence: 'exact',
    },
    price,
  }
}
