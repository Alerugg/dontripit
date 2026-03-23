import TopNav from '../layout/TopNav'
import HomeHero from './HomeHero'
import GameSpotlightGrid from './GameSpotlightGrid'
import CatalogBlueprint from './CatalogBlueprint'

export default function HomePageShell() {
  return (
    <main className="home-page">
      <TopNav />

      <div className="page-shell home-shell home-shell-v2">
        <HomeHero />
        <GameSpotlightGrid />
        <CatalogBlueprint />
      </div>

      <footer className="site-footer">
        <div className="page-shell site-footer-inner">
          <p>Don’tRipIt · Catálogo TCG con hubs por juego, búsqueda deduplicada y variantes dentro de cada carta.</p>
        </div>
      </footer>
    </main>
  )
}
