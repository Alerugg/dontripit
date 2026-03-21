import TopNav from '../../components/layout/TopNav'
import CatalogExplorer from '../../components/catalog/CatalogExplorer'

export default function ExplorerPage() {
  return (
    <main>
      <TopNav />
      <CatalogExplorer
        heading="Explorador global"
        description="Usa este modo secundario para buscar en todos los TCGs a la vez cuando necesites comparar resultados."
        kicker="Modo secundario · Multi-game"
        allowGameSelect
      />
    </main>
  )
}
