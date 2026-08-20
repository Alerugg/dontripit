import TopNav from '../../components/layout/TopNav'
import SiteFooter from '../../components/layout/SiteFooter'
import CatalogExplorer from '../../components/catalog/CatalogExplorer'

export const metadata = {
  title: 'Explorador de catálogo',
  description: 'Busca cartas, impresiones físicas y sets de todos los TCG activos en Don’tRipIt.',
  alternates: { canonical: '/explorer' },
}

export default function ExplorerPage() {
  return (
    <main className="v5-explorer-page">
      <TopNav />
      <div className="app-shell v5-explorer-wrap">
        <CatalogExplorer
          heading="Busca por carta, impresión o set"
          description="Empieza por el nombre que conoces. Las cartas canónicas, sus impresiones físicas y los sets permanecen separados para que cada resultado tenga una identidad clara."
          kicker="Explorador de catálogo"
          allowGameSelect
        />
      </div>
      <SiteFooter />
    </main>
  )
}
