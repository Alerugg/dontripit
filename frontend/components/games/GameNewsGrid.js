import './GameNewsGrid.css'

function formatDate(value) {
  if (!value) return ''
  try {
    return new Intl.DateTimeFormat('es-ES', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    }).format(new Date(value))
  } catch {
    return value
  }
}

function getNewsSummary(item = {}) {
  return item.summary || item.excerpt || item.description || 'Contenido editorial del juego.'
}

function getNewsHref(item = {}) {
  return item.href || item.url || item.link || ''
}

export default function GameNewsGrid({ news = [] }) {
  const items = news.length
    ? news
    : [
        {
          title: 'Noticias oficiales próximamente',
          summary: 'Este bloque mostrará novedades reales del TCG desde fuentes oficiales.',
          href: '',
          date: '',
          source: 'Dontripit',
          tag: 'News',
        },
      ]

  return (
    <section className="game-news-block panel">
      <div className="game-news-block-head">
        <div>
          <p className="eyebrow">Noticias</p>
          <h2>Últimas novedades del juego</h2>
        </div>
        <p className="game-news-block-copy">
          Lanzamientos, anuncios y movimientos importantes del TCG.
        </p>
      </div>

      <div className="game-news-grid">
        {items.map((item, index) => {
          const href = getNewsHref(item)
          const content = (
            <>
              <div className="game-news-card-top">
                <div className="game-news-card-meta-row">
                  {item.tag ? <span className="game-news-chip">{item.tag}</span> : null}
                  {item.source ? <span className="game-news-source">{item.source}</span> : null}
                </div>

                {(item.date || item.published_at) ? (
                  <p className="game-news-date">{formatDate(item.date || item.published_at)}</p>
                ) : null}
              </div>

              <div className="game-news-card-body">
                <h3>{item.title}</h3>
                <p>{getNewsSummary(item)}</p>
              </div>

              {href ? <span className="game-news-card-cta">Leer noticia</span> : null}
            </>
          )

          return href ? (
            <a
              key={`${href}-${index}`}
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="game-news-card"
            >
              {content}
            </a>
          ) : (
            <article key={`${item.title}-${index}`} className="game-news-card is-placeholder">
              {content}
            </article>
          )
        })}
      </div>
    </section>
  )
}
