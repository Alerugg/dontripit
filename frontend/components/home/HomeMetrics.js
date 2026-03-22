export default function HomeMetrics({ metrics = [] }) {
  return (
    <section className="home-metrics" aria-label="Indicadores clave de Don’tRipIt">
      {metrics.map((metric) => (
        <article key={metric.label} className="metric-card panel-soft">
          <span className="metric-value">{metric.value}</span>
          <strong>{metric.label}</strong>
          <p>{metric.detail}</p>
        </article>
      ))}
    </section>
  )
}
