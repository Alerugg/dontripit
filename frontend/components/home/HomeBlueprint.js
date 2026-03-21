import { homeBlueprintSteps } from './homeData'

export default function HomeBlueprint() {
  return (
    <section className="home-blueprint panel">
      <div className="section-copy section-copy-wide home-section-heading">
        <p className="kicker">Workflow del producto</p>
        <h2>Blueprint modular para pasar de descubrimiento a catálogo profundo sin rehacer la arquitectura visual.</h2>
      </div>

      <div className="home-blueprint-grid">
        {homeBlueprintSteps.map((item) => (
          <article key={item.step} className="home-blueprint-card panel-soft">
            <span className="home-blueprint-step">{item.step}</span>
            <h3>{item.title}</h3>
            <p>{item.body}</p>
          </article>
        ))}
      </div>
    </section>
  )
}
