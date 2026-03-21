const blueprint = [
  {
    title: 'TCG',
    body: 'Punto de entrada por juego con identidad, CTA y navegación dedicada.',
  },
  {
    title: 'Colección / Set',
    body: 'Contexto por expansión, set code e idioma cuando aplique.',
  },
  {
    title: 'Carta',
    body: 'Ficha limpia con datos clave, sets relacionados y acceso a todas las prints.',
  },
  {
    title: 'Detalle de variante',
    body: 'Miniatura, collector number, rareza, idioma y futuros bloques de precio/stock.',
  },
]

export default function CatalogBlueprint() {
  return (
    <section className="catalog-blueprint panel-soft">
      <div className="section-copy section-copy-wide">
        <p className="kicker">Blueprint de producto</p>
        <h2>Una estructura pensada para catálogo hoy y marketplace mañana.</h2>
      </div>

      <div className="blueprint-grid">
        {blueprint.map((item, index) => (
          <article key={item.title} className="blueprint-card panel">
            <span className="blueprint-step">0{index + 1}</span>
            <h3>{item.title}</h3>
            <p>{item.body}</p>
          </article>
        ))}
      </div>
    </section>
  )
}
