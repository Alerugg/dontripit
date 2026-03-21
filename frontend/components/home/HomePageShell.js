import TopNav from '../layout/TopNav'
import HomeHero from './HomeHero'
import GameSpotlightGrid from './GameSpotlightGrid'
import CatalogBlueprint from './CatalogBlueprint'

export default function HomePageShell() {
  return (
    <main>
      <TopNav />

      <div className="landing-shell landing-v3">
        <HomeHero />
        <GameSpotlightGrid />
        <CatalogBlueprint />
      </div>
    </main>
  )
}
