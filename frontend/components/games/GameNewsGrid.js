import './GameNewsGrid.css'

const REGION_LABELS = {
  GLOBAL: 'Global',
  US: 'USA',
  EU: 'Europa',
  JP: 'Japón',
  EN: 'Internacional',
}

function formatDate(value) {
  if (!value) return ''
  try {
    return new Intl.DateTimeFormat('es-ES', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    }).format(new Date(value))
  } catch {
    return ''
  }
}

function getNewsSummary(item = {}) {
  return item.summary || item.excerpt || item.description || 'Abre la fuente oficial para consultar todos los detalles.'
}

function getNewsHref(item = {}) {
  return item.source_url || item.href || item.url || item.link || ''
}

export default function GameNewsGrid({ news = [] }) {
  if (!news.length) {
    return (
      <section className="game-news-block panel">
        <div className="game-news-block-head">
          <div>
            <p className="eyebrow">Noticias oficiales</p>
            <h2>Últimas novedades verificadas</h2>
          </div>
          <p className="game-news-block-copy">Solo mostramos publicaciones procedentes de canales oficiales del juego.</p>
        </div>
        <div className="dri-soft-empty">
          <strong>No hay noticias oficiales verificables para mostrar ahora mismo.</strong>
          <p>Preferimos dejar este espacio vacío antes que completar el feed con fuentes comunitarias o fechas estimadas.</p>
        </div>
      </section>
    )
  }

  return (
    <section className="game-news-block panel">
      <div className="game-news-block-head">
        <div>
          <p className="eyebrow">Noticias oficiales</p>
          <h2>Últimas novedades verificadas</h2>
        </div>
        <p className="game-news-block-copy">Fuente, región y fecha se conservan tal como podemos verificarlas. Una fecha desconocida no se sustituye por “hoy”.</p>
      </div>

      <div className="game-news-grid">
        {news.map((item, index) => {
          const href = getNewsHref(item)
          const published = formatDate(item.date || item.published_at)
          const region = REGION_LABELS[item.region] || item.region || ''
          const content = (
            <>
              <div className="game-news-card-top">
                <div className="game-news-card-meta-row">
                  <span className="game-news-chip">Oficial</span>
                  {region ? <span className="game-news-region">{region}</span> : null}
                  {item.source ? <span className="game-news-source">{item.source}</span> : null}
                </div>
                {published ? <p className="game-news-date">{published}</p> : null}
              </div>

              <div className="game-news-card-body">
                <h3>{item.title}</h3>
                <p>{getNewsSummary(item)}</p>
              </div>

              {href ? <span className="game-news-card-cta">Abrir fuente oficial ↗</span> : null}
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
