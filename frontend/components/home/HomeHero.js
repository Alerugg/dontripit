export default function HomeHero() {
  return (
    <section className="home-section home-hero home-hero-v4">
      <div className="home-hero-panel home-panel">
        <div className="home-hero-copy">
          <p className="home-kicker">Don’tRipIt · TCG catalog platform</p>

          <h1>
            Explora cartas, sets y variantes
            <span> desde explorers dedicados.</span>
          </h1>

          <p className="home-hero-lead">
            Una base visual y técnica para navegar cada TCG con claridad, sin mezclar universos
            y sin duplicar variantes. Elige un juego y entra directo a su explorer.
          </p>
        </div>

        <div className="home-hero-side">
          <div className="home-hero-bundle" aria-hidden="true">
            <img
              src="/branding/tcg_bundle.png"
              alt=""
              className="home-hero-bundle-image"
            />
          </div>

          <article className="home-hero-note home-panel-soft">
            <span className="home-panel-tag">Dedicated explorers</span>
            <strong>Una entrada clara por cada TCG</strong>
            <p>
              Pokémon, Magic, Yu-Gi-Oh!, One Piece y Riftbound organizados como universos
              independientes para una navegación más limpia.
            </p>
          </article>

          <article className="home-hero-note home-panel-soft">
            <span className="home-panel-tag">Collector-first</span>
            <strong>Las variantes viven dentro de cada carta</strong>
            <p>
              La búsqueda muestra la carta principal y deja idiomas, finishes y ediciones
              dentro del detalle, donde realmente importan.
            </p>
          </article>
        </div>

        <div className="home-hero-ambient home-hero-ambient-a" />
        <div className="home-hero-ambient home-hero-ambient-b" />
      </div>
    </section>
  )
}