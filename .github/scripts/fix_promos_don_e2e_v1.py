from pathlib import Path
import re


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text()
    if old not in text:
        raise SystemExit(f"expected snippet not found in {path}: {old[:160]!r}")
    p.write_text(text.replace(old, new, 1))


def regex_replace_once(path: str, pattern: str, replacement: str) -> None:
    p = Path(path)
    text = p.read_text()
    next_text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"expected one regex replacement in {path}, got {count}")
    p.write_text(next_text)


# 1) Catalog BFF: Search V2 exact_identifier is authoritative and must never fall back to legacy.
route = "frontend/app/api/catalog/search/route.js"
replace_once(
    route,
    "\nfunction marketPrice(item) {",
    """
function normalizeV2DirectItem(item = {}) {
  if (item?.type !== 'print') return normalizeV2Card(item)
  const id = item.print_id || item.id
  return {
    ...item,
    type: 'print',
    id,
    print_id: id,
    title: item.name || item.title,
    name: item.name || item.title,
    primary_image_url: item.primary_image_url || item.image_url || null,
  }
}

function marketPrice(item) {""",
)

new_fetch_canonical = r"""async function fetchCanonicalCardSource({ q, game, requireAll = false, limit = 24, offset = 0 }) {
  const first = await callInternalApi('/api/v2/search', {
    params: { q, game, limit: requireAll ? SEARCH_BATCH : limit, offset: requireAll ? 0 : offset },
    timeoutMs: 20000,
  })
  if (!first.ok) return { ok: false, upstream: first, rows: [], total: 0, truncated: false }

  const payload = first.payload || {}
  const rawTotal = payload.total
  const exactTotal = rawTotal === null || rawTotal === undefined ? null : Number(rawTotal)
  const paginationMode = String(payload.pagination_mode || '')
  const exactIdentifierMode = paginationMode === 'exact_identifier'
  const canonicalNameMode = paginationMode === 'canonical_name' && Number.isFinite(exactTotal)

  // exact_identifier is a first-class Search V2 mode. Falling back to /api/v1/search
  // here makes certified promo/collector lookups such as P-150 disappear in the UI.
  if (!canonicalNameMode && !exactIdentifierMode) {
    const fallback = await fetchAllLegacyRows({ q, game, type: 'card' })
    return fallback.ok
      ? { ...fallback, total: fallback.rows.length, canonicalMode: false, exactIdentifierMode: false, paginationMode }
      : fallback
  }

  const normalizeItem = exactIdentifierMode ? normalizeV2DirectItem : normalizeV2Card

  if (!requireAll) {
    const rows = toItems(payload).map(normalizeItem)
    const inferredTotal = Number.isFinite(exactTotal)
      ? exactTotal
      : (payload.has_more ? offset + rows.length + 1 : offset + rows.length)
    return {
      ok: true,
      rows,
      total: inferredTotal,
      totalPrints: Number(payload.total_prints || 0),
      truncated: false,
      canonicalMode: true,
      exactIdentifierMode,
      paginationMode,
    }
  }

  const rows = toItems(payload).map(normalizeItem)
  let currentPayload = payload
  let nextOffset = Number(currentPayload.next_offset)
  while (currentPayload.has_more !== false && Number.isFinite(nextOffset) && rows.length < MAX_KIND_RESULTS) {
    const page = await callInternalApi('/api/v2/search', {
      params: { q, game, limit: SEARCH_BATCH, offset: nextOffset },
      timeoutMs: 20000,
    })
    if (!page.ok) return { ok: false, upstream: page, rows: [], total: 0, truncated: false }
    const pageItems = toItems(page.payload).map(normalizeItem)
    if (!pageItems.length) break
    rows.push(...pageItems)
    currentPayload = page.payload || {}
    const following = Number(currentPayload.next_offset)
    if (!currentPayload.has_more || !Number.isFinite(following) || following <= nextOffset) break
    nextOffset = following
  }

  const resolvedTotal = Number.isFinite(exactTotal) ? exactTotal : rows.length
  return {
    ok: true,
    rows: uniqueRows(rows).slice(0, MAX_KIND_RESULTS),
    total: resolvedTotal,
    totalPrints: Number(payload.total_prints || 0),
    truncated: rows.length < resolvedTotal,
    canonicalMode: true,
    exactIdentifierMode,
    paginationMode,
  }
}

async function enrichPrintsWithMarket"""
regex_replace_once(
    route,
    r"async function fetchCanonicalCardSource\(\{ q, game, requireAll = false, limit = 24, offset = 0 \}\) \{.*?\n\}\n\nasync function enrichPrintsWithMarket",
    new_fetch_canonical,
)
replace_once(
    route,
    "    selectedRows = (await enrichCardsWithMatchedPrintMarket(selectedRows)).rows\n",
    "    selectedRows = (await enrichPrintsWithMarket(selectedRows)).rows\n    selectedRows = (await enrichCardsWithMatchedPrintMarket(selectedRows)).rows\n",
)


# 2) Browser BFF endpoints for the certified source-owned DON backend contract.
Path("frontend/app/api/search-v2/don").mkdir(parents=True, exist_ok=True)
Path("frontend/app/api/search-v2/don/suggest").mkdir(parents=True, exist_ok=True)
Path("frontend/app/api/search-v2/don/route.js").write_text(
    r"""import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'

export async function GET(request) {
  const { searchParams } = new URL(request.url)
  const upstream = await callInternalApi('/api/v2/search/don', {
    params: {
      q: searchParams.get('q') || '',
      game: 'onepiece',
      limit: searchParams.get('limit') || 24,
      offset: searchParams.get('offset') || 0,
    },
    timeoutMs: 15000,
  })

  if (!upstream.ok) {
    const developerHint = getDeveloperErrorHint(upstream.payload, upstream.status)
    return NextResponse.json(
      {
        error: 'onepiece_don_search_failed',
        message: getPublicErrorMessage(upstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: upstream.status },
    )
  }

  return NextResponse.json(upstream.payload || { items: [], total: 0, don_only: true })
}
"""
)
Path("frontend/app/api/search-v2/don/suggest/route.js").write_text(
    r"""import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../../lib/catalog/internalApi'

export async function GET(request) {
  const { searchParams } = new URL(request.url)
  const q = String(searchParams.get('q') || '').trim()
  if (!q) return NextResponse.json({ items: [], don_only: true })

  const upstream = await callInternalApi('/api/v2/search/don/suggest', {
    params: {
      q,
      game: 'onepiece',
      limit: searchParams.get('limit') || 8,
    },
    timeoutMs: 10000,
  })

  if (!upstream.ok) {
    const developerHint = getDeveloperErrorHint(upstream.payload, upstream.status)
    return NextResponse.json(
      {
        error: 'onepiece_don_suggest_failed',
        message: getPublicErrorMessage(upstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: upstream.status },
    )
  }

  const payload = upstream.payload || { items: [] }
  const items = (payload.items || []).map((item) => ({
    ...item,
    title: item.name,
    primary_image_url: item.primary_image_url || item.image_url || null,
  }))
  return NextResponse.json({ ...payload, items })
}
"""
)


# 3) Catalog client functions for source-owned DON pages and suggestions.
client = "frontend/lib/catalog/client.js"
replace_once(
    client,
    "\nexport function fetchCardById(id) {",
    r"""
export async function searchOnePieceDonPage(filters = {}, options = {}) {
  const payload = await request('/api/search-v2/don', {
    q: filters?.q || '',
    limit: filters?.limit || 24,
    offset: filters?.offset || 0,
  }, { ...options, timeoutMs: options.timeoutMs ?? SEARCH_TIMEOUT_MS })

  return {
    items: payload?.items || [],
    total: Number(payload?.total ?? 0),
    limit: Number(payload?.limit ?? filters?.limit ?? 24),
    offset: Number(payload?.offset ?? filters?.offset ?? 0),
    has_more: Boolean(payload?.has_more),
    next_offset: payload?.next_offset ?? null,
    identity_scope: payload?.identity_scope || 'source_owned',
  }
}

export async function suggestOnePieceDon(filters = {}, options = {}) {
  const payload = await request('/api/search-v2/don/suggest', {
    q: filters?.q || '',
    limit: filters?.limit || 8,
  }, { ...options, timeoutMs: options.timeoutMs ?? SUGGEST_TIMEOUT_MS })
  return payload?.items || []
}

export function fetchCardById(id) {""",
)


# 4) Friendly source-owned DON result cards. No fake Card/Print href is ever created.
Path("frontend/components/catalog/DonMarketResults.js").write_text(
    r"""import FallbackImage from '../common/FallbackImage'
import { safeCardmarketUrl } from '../../lib/searchV2/market'
import './DonMarketResults.css'

function money(value, currency = 'EUR') {
  const numeric = Number(value)
  if (!Number.isFinite(numeric) || numeric <= 0) return null
  try {
    return new Intl.NumberFormat('es-ES', {
      style: 'currency',
      currency: currency || 'EUR',
      maximumFractionDigits: 2,
    }).format(numeric)
  } catch {
    return `${numeric.toFixed(2)} ${currency || 'EUR'}`
  }
}

export default function DonMarketResults({ items = [], total = 0 }) {
  return (
    <section className="dri-don-results" aria-label="Resultados DON!!">
      <div className="dri-don-results-head">
        <div>
          <span>One Piece · DON!!</span>
          <h2>DON!! certificados</h2>
        </div>
        <strong>{Number(total || items.length).toLocaleString('es-ES')}</strong>
      </div>
      <p className="dri-don-note">Mostramos identidades DON!! verificadas por personaje desde la fuente de mercado. No inventamos una carta o Print canónico cuando esa relación física todavía no existe.</p>
      <div className="dri-don-grid">
        {items.map((item) => {
          const price = money(item.cardmarket_price, item.cardmarket_currency || 'EUR')
          const cardmarketUrl = safeCardmarketUrl(item.cardmarket_website_path)
          return (
            <article key={`don-${item.metacard_external_id || item.representative_external_product_id}`} className="dri-don-card">
              <div className="dri-don-image-wrap">
                <FallbackImage
                  src={item.primary_image_url}
                  alt={item.name || `DON!! ${item.subject || ''}`}
                  className="catalog-image"
                  placeholderClassName="catalog-placeholder image-fallback"
                  label="DON!!"
                />
                <span className="dri-don-badge">DON!!</span>
              </div>
              <div className="dri-don-copy">
                <span className="dri-don-subject">{item.subject || 'One Piece'}</span>
                <h3>{item.name || `DON!! (${item.subject || 'One Piece'})`}</h3>
                <p>{item.product_count ? `${item.product_count} producto${item.product_count === 1 ? '' : 's'} de mercado asociado${item.product_count === 1 ? '' : 's'}` : 'Identidad de mercado certificada'}</p>
                <div className="dri-don-market">
                  <div>
                    <small>Cardmarket</small>
                    <strong>{price || 'Sin Price Guide actual'}</strong>
                  </div>
                  {cardmarketUrl ? (
                    <a href={cardmarketUrl} target="_blank" rel="noopener noreferrer sponsored">Ver en Cardmarket ↗</a>
                  ) : null}
                </div>
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}
"""
)
Path("frontend/components/catalog/DonMarketResults.css").write_text(
    r""".dri-don-results { display: grid; gap: 16px; }
.dri-don-results-head { display: flex; align-items: end; justify-content: space-between; gap: 16px; }
.dri-don-results-head span, .dri-don-subject { color: var(--muted, #667085); font-size: .76rem; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
.dri-don-results-head h2 { margin: 3px 0 0; font-size: clamp(1.35rem, 2vw, 1.9rem); }
.dri-don-results-head > strong { font-size: 1.15rem; }
.dri-don-note { margin: 0; max-width: 850px; color: var(--muted, #667085); line-height: 1.55; }
.dri-don-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 16px; }
.dri-don-card { overflow: hidden; border: 1px solid rgba(148,163,184,.24); border-radius: 18px; background: var(--panel, #fff); box-shadow: 0 10px 28px rgba(15,23,42,.06); }
.dri-don-image-wrap { position: relative; aspect-ratio: 5 / 7; background: rgba(148,163,184,.08); }
.dri-don-image-wrap .catalog-image { width: 100%; height: 100%; object-fit: contain; }
.dri-don-badge { position: absolute; top: 10px; left: 10px; padding: 6px 9px; border-radius: 999px; background: #111827; color: #fff; font-size: .72rem; font-weight: 900; letter-spacing: .06em; }
.dri-don-copy { display: grid; gap: 8px; padding: 15px; }
.dri-don-copy h3 { margin: 0; font-size: 1.05rem; }
.dri-don-copy p { margin: 0; min-height: 2.7em; color: var(--muted, #667085); font-size: .88rem; line-height: 1.45; }
.dri-don-market { display: flex; align-items: end; justify-content: space-between; gap: 12px; padding-top: 10px; border-top: 1px solid rgba(148,163,184,.18); }
.dri-don-market div { display: grid; gap: 2px; }
.dri-don-market small { color: var(--muted, #667085); }
.dri-don-market a { font-size: .82rem; font-weight: 800; text-decoration: none; }
@media (max-width: 640px) { .dri-don-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; } .dri-don-copy { padding: 12px; } .dri-don-market { align-items: start; flex-direction: column; } }
"""
)


# 5) CatalogExplorer: wire the One Piece-only DON filter into the main Explorer.
explorer = "frontend/components/catalog/CatalogExplorer.js"
replace_once(
    explorer,
    "import CatalogResults from './ResultsGrid'\n",
    "import CatalogResults from './ResultsGrid'\nimport DonMarketResults from './DonMarketResults'\n",
)
replace_once(
    explorer,
    "import { fetchCatalogCounts, searchCatalogPage as searchCatalog, suggestCatalog } from '../../lib/catalog/client'",
    "import { fetchCatalogCounts, searchCatalogPage as searchCatalog, searchOnePieceDonPage, suggestCatalog, suggestOnePieceDon } from '../../lib/catalog/client'",
)
replace_once(
    explorer,
    "  pricedOnly,\n  setPricedOnly,\n  currentGameConfig,\n}) {",
    "  pricedOnly,\n  setPricedOnly,\n  donOnly,\n  setDonOnly,\n  donOnlyAvailable,\n  currentGameConfig,\n}) {",
)
replace_once(
    explorer,
    """      <div className="filter-group v13-exact-price-filter">
        <label className="checkbox-row">
          <input type="checkbox" checked={pricedOnly} disabled={!physicalFiltersActive} onChange={(event) => setPricedOnly(event.target.checked)} />
          <span>Solo impresiones con precio exacto</span>
        </label>
        <small className="v6-filter-hint">No usamos el precio de otra edición para completar huecos.</small>
      </div>

      {currentGameConfig && (""",
    """      <div className="filter-group v13-exact-price-filter">
        <label className="checkbox-row">
          <input type="checkbox" checked={pricedOnly} disabled={!physicalFiltersActive} onChange={(event) => setPricedOnly(event.target.checked)} />
          <span>Solo impresiones con precio exacto</span>
        </label>
        <small className="v6-filter-hint">No usamos el precio de otra edición para completar huecos.</small>
      </div>

      {donOnlyAvailable ? (
        <div className="filter-group v13-exact-price-filter">
          <label className="checkbox-row">
            <input type="checkbox" checked={donOnly} onChange={(event) => setDonOnly(event.target.checked)} />
            <span>Solo DON!!</span>
          </label>
          <small className="v6-filter-hint">Filtra por personaje usando únicamente identidades DON!! certificadas. No mezcla cartas normales ni inventa Print ID.</small>
        </div>
      ) : null}

      {currentGameConfig && (""",
)
replace_once(
    explorer,
    "  initialPricedOnly = false,\n  initialPage = 1,\n}) {",
    "  initialPricedOnly = false,\n  initialDonOnly = false,\n  initialPage = 1,\n}) {",
)
replace_once(
    explorer,
    "  const [pricedOnly, setPricedOnly] = useState(Boolean(initialPricedOnly))\n  const [page, setPage]",
    "  const [pricedOnly, setPricedOnly] = useState(Boolean(initialPricedOnly))\n  const [donOnly, setDonOnly] = useState(Boolean(initialDonOnly) && (scopedGame || initialGame) === 'onepiece')\n  const [page, setPage]",
)
replace_once(
    explorer,
    "  }, [submittedQuery, game, scopedGame, type, sort, language, pricedOnly])\n\n  useEffect(() => {\n    if (!debouncedInput) {",
    """  }, [submittedQuery, game, scopedGame, type, sort, language, pricedOnly, donOnly])

  useEffect(() => {
    const activeGame = scopedGame || game
    if (activeGame !== 'onepiece' && donOnly) setDonOnly(false)
  }, [donOnly, game, scopedGame])

  useEffect(() => {
    if (!donOnly) return
    setType('')
    setSort('relevance')
    setLanguage('')
    setPricedOnly(false)
    setView('grid')
  }, [donOnly])

  useEffect(() => {
    if (!debouncedInput) {""",
)
replace_once(
    explorer,
    """        const nextSuggestions = await suggestCatalog(
          { q: debouncedInput, game: scopedGame || game, limit: 8 },
          { signal: controller.signal },
        )""",
    """        const nextSuggestions = donOnly
          ? await suggestOnePieceDon({ q: debouncedInput, limit: 8 }, { signal: controller.signal })
          : await suggestCatalog(
              { q: debouncedInput, game: scopedGame || game, limit: 8 },
              { signal: controller.signal },
            )""",
)
replace_once(
    explorer,
    "  }, [debouncedInput, game, scopedGame])\n\n  useEffect(() => {",
    "  }, [debouncedInput, donOnly, game, scopedGame])\n\n  useEffect(() => {",
)
replace_once(
    explorer,
    """      try {
        const result = await searchCatalog({
          ...filters,
          include_counts: deferCounts ? 0 : 1,
        }, { signal: controller.signal })

        if (cancelled) return

        setItems(result.items)
        setTotal(result.total)
        setCounts(result.counts)
        setTruncated(result.truncated)
        setIntegrity(result.integrity || '')
        setLoading(false)

        if (!result.counts_complete) {""",
    """      try {
        if (donOnly) {
          const result = await searchOnePieceDonPage({
            q: submittedQuery,
            limit: PAGE_SIZE,
            offset: page * PAGE_SIZE,
          }, { signal: controller.signal })
          if (cancelled) return
          setItems(result.items)
          setTotal(result.total)
          setCounts({ card: 0, print: 0, set: 0, all: result.total })
          setTruncated(false)
          setIntegrity('')
          setLoading(false)
          return
        }

        const result = await searchCatalog({
          ...filters,
          include_counts: deferCounts ? 0 : 1,
        }, { signal: controller.signal })

        if (cancelled) return

        setItems(result.items)
        setTotal(result.total)
        setCounts(result.counts)
        setTruncated(result.truncated)
        setIntegrity(result.integrity || '')
        setLoading(false)

        if (!result.counts_complete) {""",
)
replace_once(
    explorer,
    "  }, [submittedQuery, game, scopedGame, type, language, pricedOnly, sort, page])",
    "  }, [submittedQuery, game, scopedGame, type, language, pricedOnly, sort, page, donOnly])",
)
replace_once(
    explorer,
    "    if (pricedOnly && (type === 'print' || type === '')) params.set('priced', '1')\n    if (page > 0)",
    "    if (pricedOnly && (type === 'print' || type === '')) params.set('priced', '1')\n    if (donOnly) params.set('don', '1')\n    if (page > 0)",
)
replace_once(
    explorer,
    "  }, [game, language, page, pathname, pricedOnly, scopedGame, sort, submittedQuery, type, view])",
    "  }, [donOnly, game, language, page, pathname, pricedOnly, scopedGame, sort, submittedQuery, type, view])",
)
replace_once(
    explorer,
    """  const handleSuggestionSelect = (item) => {
    const title = item.title || item.name || ''
    const href = resolveSuggestionHref(item)
    setInputValue(title)
    setSuggestions([])

    if (href) {""",
    """  const handleSuggestionSelect = (item) => {
    const title = item.title || item.name || ''
    if (item.type === 'don_market') {
      const subject = item.subject || title
      setInputValue(subject)
      setSuggestions([])
      if (subject) setSubmittedQuery(subject)
      return
    }
    const href = resolveSuggestionHref(item)
    setInputValue(title)
    setSuggestions([])

    if (href) {""",
)
replace_once(
    explorer,
    "    if (!submittedQuery && !type) {\n      setType('card')",
    "    if (!donOnly && !submittedQuery && !type) {\n      setType('card')",
)
replace_once(
    explorer,
    """  const physicalFiltersActive = type === 'print' || type === ''
  const activeFilterCount = (allowGameSelect && game ? 1 : 0) + (language ? 1 : 0) + (pricedOnly ? 1 : 0)
  const filterProps = {""",
    """  const donOnlyAvailable = currentGame === 'onepiece'
  const physicalFiltersActive = !donOnly && (type === 'print' || type === '')
  const activeFilterCount = (allowGameSelect && game ? 1 : 0) + (language ? 1 : 0) + (pricedOnly ? 1 : 0) + (donOnly ? 1 : 0)
  const filterProps = {""",
)
replace_once(
    explorer,
    "    pricedOnly,\n    setPricedOnly,\n    currentGameConfig,\n  }",
    "    pricedOnly,\n    setPricedOnly,\n    donOnly,\n    setDonOnly,\n    donOnlyAvailable,\n    currentGameConfig,\n  }",
)
replace_once(
    explorer,
    "            placeholder={currentGame ? `Busca dentro de ${currentGameConfig?.name || currentGame}` : 'Pikachu, Luffy, Black Lotus, Dark Magician…'}\n",
    "            placeholder={donOnly ? 'Luffy, Zoro, DON…' : currentGame ? `Busca dentro de ${currentGameConfig?.name || currentGame}` : 'Pikachu, Luffy, Black Lotus, Dark Magician…'}\n",
)
regex_replace_once(
    explorer,
    r'\n        <div className="v5-result-tabs v13-result-tabs" role="tablist" aria-label="Tipo de resultado">.*?\n        </div>\n      </div>',
    r'''
        {!donOnly ? (
          <div className="v5-result-tabs v13-result-tabs" role="tablist" aria-label="Tipo de resultado">
            {RESULT_TYPES.map((option) => (
              <button
                key={option.value || 'all'}
                type="button"
                role="tab"
                aria-selected={type === option.value}
                className={`v5-result-tab ${type === option.value ? 'is-active' : ''}`}
                onClick={() => changeType(option.value)}
              >
                {option.label}
                <span className="v7-result-count">{formatCount(counts[option.countKey])}</span>
              </button>
            ))}
          </div>
        ) : (
          <div className="v7-integrity-note" role="status">Solo DON!! activo · resultados source-owned certificados</div>
        )}
      </div>''',
)
replace_once(
    explorer,
    """              <label className="v13-sort-control">
                <span>Ordenar</span>
                <select className="input" value={sort} onChange={(event) => setSort(event.target.value)}>
                  {availableSorts.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </label>
""",
    """              {!donOnly ? (
                <label className="v13-sort-control">
                  <span>Ordenar</span>
                  <select className="input" value={sort} onChange={(event) => setSort(event.target.value)}>
                    {availableSorts.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </select>
                </label>
              ) : null}
""",
)
replace_once(
    explorer,
    """          {submittedQuery && !loading && !error && total === 0 && <StatePanel title="Sin resultados por ahora" description="Prueba otro término, cambia los filtros o vuelve al explorador global." />}
          {!loading && !error && items.length > 0 && <CatalogResults items={items} view={view} />}
""",
    """          {submittedQuery && !loading && !error && total === 0 && <StatePanel title="Sin resultados por ahora" description={donOnly ? 'No encontramos un DON!! certificado para ese personaje o término.' : 'Prueba otro término, cambia los filtros o vuelve al explorador global.'} />}
          {!loading && !error && items.length > 0 && (donOnly ? <DonMarketResults items={items} total={total} /> : <CatalogResults items={items} view={view} />)}
""",
)


# 6) Preserve DON filter in scoped game URL state.
game_page = "frontend/app/games/[slug]/page.js"
replace_once(
    game_page,
    "    pricedOnly: query?.priced === '1',\n    page: positivePage(query?.page),",
    "    pricedOnly: query?.priced === '1',\n    donOnly: game.slug === 'onepiece' && query?.don === '1',\n    page: positivePage(query?.page),",
)
game_hub = "frontend/components/games/GameHubPage.js"
replace_once(
    game_hub,
    "            initialPricedOnly={Boolean(initialExplorerState.pricedOnly)}\n            initialPage={initialExplorerState.page || 1}",
    "            initialPricedOnly={Boolean(initialExplorerState.pricedOnly)}\n            initialDonOnly={Boolean(initialExplorerState.donOnly)}\n            initialPage={initialExplorerState.page || 1}",
)


# 7) Suggestions clearly identify source-owned DON rows.
suggestion = "frontend/components/catalog/SuggestionRow.js"
replace_once(
    suggestion,
    """function typeLabel(type) {
  if (type === 'set') return 'Set'
  if (type === 'print') return 'Print'
  return 'Carta'
}""",
    """function typeLabel(type) {
  if (type === 'set') return 'Set'
  if (type === 'print') return 'Print'
  if (type === 'don_market') return 'DON!!'
  return 'Carta'
}""",
)
replace_once(
    suggestion,
    """function actionLabel(item) {
  if (item.type === 'set') return 'Abrir set'
  if (item.type === 'print' || item.type === 'card') return 'Abrir carta'
  return 'Buscar'
}""",
    """function actionLabel(item) {
  if (item.type === 'set') return 'Abrir set'
  if (item.type === 'print' || item.type === 'card') return 'Abrir carta'
  if (item.type === 'don_market') return 'Buscar DON!!'
  return 'Buscar'
}""",
)


# 8) Lock behavior with source-contract tests plus backend normalization tests.
Path("frontend/tests/promos-don-search-contract.test.mjs").write_text(
    r"""import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

const read = (path) => fs.readFile(new URL(path, import.meta.url), 'utf8')

test('catalog BFF treats exact_identifier as authoritative Search V2 output', async () => {
  const route = await read('../app/api/catalog/search/route.js')
  assert.match(route, /paginationMode === 'exact_identifier'/)
  assert.match(route, /normalizeV2DirectItem/)
  assert.match(route, /enrichPrintsWithMarket\(selectedRows\)/)
  assert.match(route, /Falling back to \/api\/v1\/search[\s\S]*P-150/)
})

test('One Piece Explorer exposes dedicated source-owned DON search without mixing canonical cards', async () => {
  const explorer = await read('../components/catalog/CatalogExplorer.js')
  const client = await read('../lib/catalog/client.js')
  const donRoute = await read('../app/api/search-v2/don/route.js')
  const donSuggestRoute = await read('../app/api/search-v2/don/suggest/route.js')
  const donResults = await read('../components/catalog/DonMarketResults.js')

  assert.match(explorer, /Solo DON!!/)
  assert.match(explorer, /searchOnePieceDonPage/)
  assert.match(explorer, /suggestOnePieceDon/)
  assert.match(client, /\/api\/search-v2\/don/)
  assert.match(donRoute, /callInternalApi\('\/api\/v2\/search\/don'/)
  assert.match(donSuggestRoute, /callInternalApi\('\/api\/v2\/search\/don\/suggest'/)
  assert.match(donResults, /No inventamos una carta o Print canónico/)
  assert.doesNotMatch(donResults, /href=\{`\/prints\//)
})

test('DON URL state is only restorable inside One Piece', async () => {
  const gamePage = await read('../app/games/[slug]/page.js')
  assert.match(gamePage, /donOnly: game\.slug === 'onepiece' && query\?\.don === '1'/)
})
"""
)

Path("backend/tests/test_onepiece_promo_query_normalization.py").write_text(
    r"""import pytest

from app.search_v2.normalization import normalize_onepiece_collector_number


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("P-150", "p-150"),
        ("P150", "p-150"),
        ("P 150", "p-150"),
        ("p_150", "p-150"),
        ("OP05-119", "op05-119"),
        ("OP05119", "op05-119"),
        ("OP 05 119", "op05-119"),
    ],
)
def test_onepiece_collector_input_variants_normalize_to_exact_identity(raw, expected):
    assert normalize_onepiece_collector_number(raw) == expected
"""
)
