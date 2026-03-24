export default function CatalogBlueprint() {
  const benefits = [
    {
      eyebrow: 'Explorers dedicados',
      title: 'Cada TCG tiene su propia entrada',
      body: 'La navegación empieza por juego para mantener el contexto limpio y evitar mezclar universos.',
    },
    {
      eyebrow: 'Collector-first',
      title: 'Las variantes viven dentro de la carta',
      body: 'La búsqueda prioriza la carta principal y deja idiomas, finishes y ediciones dentro del detalle.',
    },
    {
      eyebrow: 'Catálogo claro',
      title: 'Sets, cartas y prints con jerarquía real',
      body: 'La estructura está pensada para discovery serio hoy y para pricing o marketplace mañana.',
    },
    {
      eyebrow: 'API ready',
      title: 'Una base técnica preparada para crecer',
      body: 'El catálogo y la API ya están alineados para integraciones, herramientas y superficies futuras.',
    },
  ]

  const metrics = [
    { value: '5', label: 'TCGs activos' },
    { value: '39k+', label: 'sets indexados' },
    { value: '53k+', label: 'cartas maestras' },
    { value: '156k+', label: 'prints y variantes' },
  ]

  return (
    <section className="home-info-stack">
      <section className="home-benefits panel">
        <div className="section-copy section-copy-wide">
          <p className="kicker">Por qué Don’tRipIt</p>
          <h2>Una base pensada para navegar TCGs con claridad.</h2>
          <p>
            El objetivo no es mezclar todo en una sola bolsa, sino crear explorers dedicados, lectura limpia
            de variantes y una base sólida para catálogo, API y marketplace.
          </p>
        </div>

        <div className="home-benefits-grid">
          {benefits.map((item) => (
            <article key={item.title} className="home-benefit-card panel-soft">
              <p className="home-benefit-eyebrow">{item.eyebrow}</p>
              <h3>{item.title}</h3>
              <p>{item.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="home-metrics-strip panel-soft">
        {metrics.map((item) => (
          <article key={item.label} className="home-metric-block">
            <strong>{item.value}</strong>
            <span>{item.label}</span>
          </article>
        ))}
      </section>
    </section>
  )
}