import TopNav from '../layout/TopNav'
import SiteFooter from '../layout/SiteFooter'
import HomeHero from './HomeHero'
import GameSpotlightGrid from './GameSpotlightGrid'
import CatalogBlueprint from './CatalogBlueprint'

export default function HomePageShell() {
  return (
    <main className="home-page">
      <TopNav />

      <div className="page-shell home-shell home-shell-v3">
        <HomeHero />
        <GameSpotlightGrid />
        <CatalogBlueprint />
      </div>

      <SiteFooter />
    </main>
  )
}