export default function GameTournamentsByRegion({ tournaments = [] }) {
  const items = tournaments.length ? tournaments : [
    { region: 'NA', title: 'Regional placeholder' },
    { region: 'EU', title: 'Championship placeholder' },
    { region: 'LATAM', title: 'Store circuit placeholder' },
  ]

  return (
    <section className="game-section panel-soft">
      <div className="section-heading compact">
        <p className="eyebrow">Torneos por región</p>
        <h2>Sección placeholder preparada para API.</h2>
      </div>
      <div className="placeholder-grid">
        {items.map((item) => (
          <article key={`${item.region}-${item.title}`} className="placeholder-card">
            <span className="pill">{item.region}</span>
            <strong>{item.title}</strong>
            <p>Esta tarjeta aceptará fechas, venue, formato y enlaces oficiales.</p>
          </article>
        ))}
      </div>
    </section>
  )
}
