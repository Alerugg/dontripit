import Link from 'next/link'
import TopNav from '../components/layout/TopNav'
import HomeHero from '../components/home/HomeHero'
import HomeMetrics from '../components/home/HomeMetrics'
import HomeGameGrid from '../components/home/HomeGameGrid'
import HomeBlueprint from '../components/home/HomeBlueprint'
import HomeWhySection from '../components/home/HomeWhySection'
import HomeFinalCta from '../components/home/HomeFinalCta'

const homeMetrics = [
  {
    value: '5 TCGs',
    label: 'hubs activos',
    detail: 'Entradas reales para Pokémon, MTG, Yu-Gi-Oh!, One Piece Card Game y Riftbound.',
  },
  {
    value: 'Set → Card → Print',
    label: 'jerarquía clara',
    detail: 'La home presenta la estructura real del catálogo para navegar variantes con contexto.',
  },
  {
    value: 'Marketplace-ready',
    label: 'base preparada',
    detail: 'Wishlist, colección, pricing y sellers pueden crecer sobre la misma UX sin rehacerla.',
  },
]

export default function HomePage() {
  return (
    <main className="home-v3">
      <TopNav />

      <div className="landing-shell home-v3-shell">
        <HomeHero />
        <HomeMetrics homeMetrics={homeMetrics} />
        <HomeGameGrid />
        <HomeBlueprint />
        <HomeWhySection />
        <HomeFinalCta />
      </div>

      <footer className="home-footer">
        <div className="home-footer-inner">
          <div>
            <p className="home-footer-brand">Don’tRipIt</p>
            <p className="home-footer-copy">Catálogo TCG premium con hubs, explorers y base lista para colección y marketplace.</p>
          </div>

          <nav className="home-footer-links" aria-label="Footer navigation">
            <Link href="/games/pokemon">Pokémon hub</Link>
            <Link href="/explorer">Explorer global</Link>
            <Link href="/admin/api-console">Admin Console</Link>
          </nav>
        </div>
      </footer>
    </main>
  )
}
