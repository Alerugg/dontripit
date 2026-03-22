export default function GameCollectionsList({ collections = [] }) {
  return (
    <section className="game-section panel-soft">
      <div className="section-heading compact">
        <p className="eyebrow">Colecciones</p>
        <h2>Listado navegable</h2>
      </div>
      <div className="placeholder-grid">
        {(collections.length ? collections : [
          { name: 'Base Set / Core' },
          { name: 'Promos & Special Releases' },
          { name: 'Competitive Staples' },
        ]).map((collection) => (
          <article key={collection.name} className="placeholder-card">
            <strong>{collection.name}</strong>
            <p>Preparado para conectar la API de colecciones y navegación por sets.</p>
          </article>
        ))}
      </div>
    </section>
  )
}
