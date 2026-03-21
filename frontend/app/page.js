import TopNav from '../components/layout/TopNav'
import HomeBlueprint from '../components/home/HomeBlueprint'
import HomeFinalCta from '../components/home/HomeFinalCta'
import HomeGameGrid from '../components/home/HomeGameGrid'
import HomeHero from '../components/home/HomeHero'
import HomeMetrics from '../components/home/HomeMetrics'
import HomeWhySection from '../components/home/HomeWhySection'

export default function HomePage() {
  return (
    <main>
      <TopNav />

      <div className="landing-shell home-v2-shell">
        <HomeHero />
        <HomeMetrics />
        <HomeGameGrid />
        <HomeBlueprint />
        <HomeWhySection />
        <HomeFinalCta />
      </div>
    </main>
  )
}
