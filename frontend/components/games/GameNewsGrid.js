export default function GameNewsGrid({ news = [] }) {
  const items = news.length ? news : [
    { title: 'Meta update placeholder' },
    { title: 'Release notes placeholder' },
    { title: 'Marketplace digest placeholder' },
  ]

  return (
    <section className="game-section panel-soft">
      <div className="section-heading compact">
        <p className="eyebrow">Noticias</p>
        <h2>Grid placeholder listo para API v2.</h2>
      </div>
      <div className="placeholder-grid">
        {items.map((item) => (
          <article key={item.title} className="placeholder-card">
            <strong>{item.title}</strong>
            <p>Espacio reservado para titulares, thumbnail, fecha y CTA editorial.</p>
          </article>
        ))}
      </div>
    </section>
  )
}
