import Link from 'next/link'

export default function Home() {
  return (
    <main className="catalog-page" style={{ minHeight: '100vh', placeContent: 'center' }}>
      <section className="panel" style={{ textAlign: 'center' }}>
        <p className="eyebrow">API-PROJECT</p>
        <h1>Frontend catálogo TCG</h1>
        <p className="hero-subtitle">Experiencia visual estilo marketplace, consumiendo la API real.</p>
        <div style={{ display: 'flex', gap: 12, justifyContent: 'center', marginTop: 16 }}>
          <Link className="primary-btn" href="/explorer">Abrir Explorer</Link>
          <Link className="ghost-btn" href="/console">API Console</Link>
        </div>
      </section>
    </main>
  )
}
