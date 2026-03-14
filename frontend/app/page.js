import Link from 'next/link'
import TopNav from '../components/layout/TopNav'

export default function HomePage() {
  return (
    <main>
      <TopNav />

      <section className="landing-shell">
        <div className="landing-hero panel">
          <p className="kicker">Don’tRipIt · Multi-game TCG catalog</p>
          <h1>Explora cartas, prints y sets con una experiencia más rápida.</h1>
          <p>
            Busca en Pokémon, MTG, Yu-Gi-Oh! y Riftbound con un flujo optimizado: sugerencias ligeras para descubrir
            y búsqueda intencional para resultados completos.
          </p>
          <div className="landing-actions">
            <Link href="/explorer" className="primary-btn">Explorar catálogo</Link>
            <Link href="/explorer" className="back-link">Browse catalog</Link>
          </div>
        </div>
      </section>
    </main>
  )
}
