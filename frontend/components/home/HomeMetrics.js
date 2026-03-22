export default function HomeMetrics({ homeMetrics }) {
  const metrics = Array.isArray(homeMetrics) ? homeMetrics : []

  return (
    <section className="home-metrics-band panel-soft" aria-label="Métricas destacadas de Don’tRipIt">
      {metrics.map((metric) => (
        <article key={metric.label} className="home-metric-item">
          <strong>{metric.value}</strong>
          <span>{metric.label}</span>
          <p>{metric.detail}</p>
        </article>
      ))}
    </section>
  )
}
