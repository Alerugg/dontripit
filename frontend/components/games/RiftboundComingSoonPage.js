export default function RiftboundComingSoonPage({ game }) {
  return (
    <section className="page-shell game-page" style={{ '--game-accent': game.accent }}>
      <header className="game-hero panel" style={{ '--game-accent': game.accent }}>
        <div className="game-hero-copy">
          <p className="eyebrow">{game.eyebrow}</p>
          <h1>{game.name}</h1>
          <p>
            Estamos preparando la integración del catálogo oficial de Riftbound. Preferimos no publicar datos incompletos o de fuentes no canónicas.
          </p>

          <div className="game-hero-insights">
            <div className="hero-insight-chip">
              <span>Próximamente</span>
              <strong>Catálogo oficial</strong>
            </div>
            <div className="hero-insight-chip">
              <span>En preparación</span>
              <strong>Búsqueda y variantes</strong>
            </div>
            <div className="hero-insight-chip">
              <span>Don’tRipIt</span>
              <strong>Sin datos de relleno</strong>
            </div>
          </div>
        </div>

        <div className="game-hero-meta panel-soft">
          <article className="hero-card panel-soft hero-card-b">
            <span>Estado del catálogo</span>
            <strong>Integración oficial pendiente.</strong>
            <small>Riftbound se activará cuando el catálogo pueda publicarse con la misma calidad que Pokémon, Magic, One Piece y Yu-Gi-Oh!.</small>
          </article>
        </div>
      </header>
    </section>
  )
}
