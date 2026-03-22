import { homeFeatureList, homeFeaturePanels } from './homeData'

export default function HomeFeatureShowcase() {
  return (
    <section className="home-section home-feature-showcase">
      <div className="home-grid-2 home-feature-layout">
        <div className="home-feature-copy">
          <p className="home-kicker">Feature showcase</p>
          <h2>Beneficios reales para discovery, organización de catálogo y escalado hacia marketplace.</h2>
          <div className="home-feature-list">
            {homeFeatureList.map((item) => (
              <article key={item} className="home-feature-item home-panel-soft">
                <span className="home-feature-bullet" aria-hidden="true" />
                <p>{item}</p>
              </article>
            ))}
          </div>
        </div>

        <div className="home-feature-visual home-panel">
          <div className="home-glow-orb home-glow-cyan" />
          <div className="home-glow-orb home-glow-violet feature-violet" />

          <div className="home-control-panel home-panel-strong">
            <div className="home-control-header">
              <span className="home-panel-tag">Product wall</span>
              <strong>Catalog control panel</strong>
            </div>

            <div className="home-control-hero">
              <div>
                <span className="home-panel-tag">Data clarity</span>
                <h3>Todo preparado para comparar variantes y leer señales clave.</h3>
              </div>
              <div className="home-control-badges">
                <span>Autocomplete</span>
                <span>Linked variants</span>
                <span>Marketplace-ready</span>
              </div>
            </div>

            <div className="home-control-grid">
              {homeFeaturePanels.map((panel) => (
                <article key={panel.label} className="home-control-card home-panel-soft">
                  <span className="home-panel-tag">{panel.label}</span>
                  <strong>{panel.value}</strong>
                  <p>{panel.note}</p>
                </article>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
