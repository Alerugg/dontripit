export default function HomeIntroGrid() {
  return (
    <section className="home-section home-intro-grid">
      <article className="home-panel home-intro-card home-intro-card-wide">
        <p className="home-kicker">Catálogo premium</p>
        <h2>Una home pensada como puerta de entrada real al producto, no como splash page genérica.</h2>
        <p>
          El nuevo layout prioriza jerarquía visual, paneles respirados y rutas útiles para pasar de descubrimiento
          editorial a exploración concreta por juego.
        </p>
      </article>

      <article className="home-panel-soft home-intro-card">
        <span className="home-panel-tag">Explorer-first</span>
        <strong>CTAs y enlaces conectados con superficies activas.</strong>
      </article>

      <article className="home-panel-soft home-intro-card">
        <span className="home-panel-tag">Market-ready</span>
        <strong>La base visual ya contempla stock, pricing y seller tooling.</strong>
      </article>
    </section>
  )
}
