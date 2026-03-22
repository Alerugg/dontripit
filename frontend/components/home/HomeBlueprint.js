import { homeBlueprintSteps } from './homeData'

export default function HomeBlueprint() {
  return (
    <section className="home-section home-blueprint">
      <div className="home-section-heading">
        <p className="home-kicker">Product blueprint</p>
        <h2>Una arquitectura jerárquica para navegar TCG, set, carta y variante con claridad de producto real.</h2>
      </div>

      <div className="home-blueprint-grid home-blueprint-track">
        {homeBlueprintSteps.map((item) => (
          <article key={item.step} className="home-panel-soft home-blueprint-card">
            <span className="home-blueprint-step">{item.step}</span>
            <h3>{item.title}</h3>
            <p>{item.body}</p>
          </article>
        ))}
      </div>
    </section>
  )
}
