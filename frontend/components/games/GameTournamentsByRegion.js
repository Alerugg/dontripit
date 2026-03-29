import './GameTournamentsByRegion.css'

export default function GameTournamentsByRegion({ tournaments = [] }) {
  const items = tournaments.length
    ? tournaments
    : [
        { region: 'NA', title: 'Regional calendar próximamente' },
        { region: 'EU', title: 'Championship calendar próximamente' },
        { region: 'LATAM', title: 'Circuitos y eventos próximamente' },
      ]

  return (
    <section className="game-tournaments-block panel-soft">
      <div className="game-tournaments-block-head">
        <div>
          <p className="eyebrow">Torneos</p>
          <h2>Eventos por región</h2>
        </div>
        <p className="game-tournaments-block-copy">
          Esta sección queda preparada para una segunda fase de calendario competitivo.
        </p>
      </div>

      <div className="game-tournaments-grid">
        {items.map((item) => (
          <article key={`${item.region}-${item.title}`} className="game-tournaments-card">
            <span className="game-tournaments-pill">{item.region}</span>
            <strong>{item.title}</strong>
            <p>Más adelante aquí irán fechas, formato, venue y enlaces oficiales.</p>
          </article>
        ))}
      </div>
    </section>
  )
}