import { homeMetrics } from './homeData'

export default function HomeMetrics() {
  return (
    <section className="home-metrics-band panel-soft" aria-label="Métricas destacadas de Don’tRipIt">
      {homeMetrics.map((metric) => (
        <article key={metric.label} className="home-metric-item">
          <strong>{metric.value}</strong>
          <span>{metric.label}</span>
          <p>{metric.detail}</p>
        </article>
      ))}
    </section>
  )
}
