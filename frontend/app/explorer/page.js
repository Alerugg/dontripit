import TopNav from '../../components/layout/TopNav'
import SiteFooter from '../../components/layout/SiteFooter'
import CatalogExplorer from '../../components/catalog/CatalogExplorer'

const VALID_TYPES = new Set(['', 'card', 'print', 'set'])
const VALID_VIEWS = new Set(['grid', 'list'])
const VALID_SORTS = new Set(['relevance', 'price_desc', 'price_asc', 'collector_asc', 'collector_desc', 'name_asc', 'name_desc'])
const VALID_LANGUAGES = new Set(['', 'en', 'es', 'ja', 'fr', 'de', 'it', 'pt'])

function positivePage(value) {
  const parsed = Number.parseInt(String(value || '1'), 10)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 1
}

export const metadata = {
  title: 'Explorador de catálogo',
  description: 'Busca cartas, impresiones físicas y sets de todos los TCG activos en Don’tRipIt.',
  alternates: { canonical: '/explorer' },
}

export default async function ExplorerPage({ searchParams }) {
  const params = await searchParams
  const query = String(params?.q || '').trim()
  const kind = VALID_TYPES.has(params?.kind || '') ? (params?.kind || '') : ''
  const view = VALID_VIEWS.has(params?.view || '') ? params.view : 'grid'
  const sort = VALID_SORTS.has(params?.sort || '') ? params.sort : 'relevance'
  const language = VALID_LANGUAGES.has(params?.language || '') ? (params?.language || '') : ''
  const game = String(params?.game || '').trim()
  const pricedOnly = params?.priced === '1'
  const page = positivePage(params?.page)

  return (
    <main className="v5-explorer-page">
      <TopNav />
      <div className="app-shell v5-explorer-wrap">
        <CatalogExplorer
          heading="Busca por carta, impresión o set"
          description="Empieza por el nombre que conoces. Las cartas canónicas, sus impresiones físicas y los sets permanecen separados para que cada resultado tenga una identidad clara."
          kicker="Explorador de catálogo"
          allowGameSelect
          initialQuery={query}
          initialType={kind}
          initialView={view}
          initialSort={sort}
          initialLanguage={language}
          initialGame={game}
          initialPricedOnly={pricedOnly}
          initialPage={page}
        />
      </div>
      <SiteFooter />
    </main>
  )
}
