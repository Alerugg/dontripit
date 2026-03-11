'use client'

function Suggestion({ item }) {
  return (
    <>
      <div className="thumb-xs">
        {item.primary_image_url ? <img src={item.primary_image_url} alt={item.title} /> : <span>TCG</span>}
      </div>
      <div className="suggestion-copy">
        <strong>{item.title}</strong>
        <small>
          {item.type}
          {item.set_code ? ` · ${item.set_code}` : ''}
          {item.collector_number ? ` · #${item.collector_number}` : ''}
        </small>
      </div>
    </>
  )
}

export default function AutocompleteList({ items, loading, query, highlightedIndex, onHover, onSelect }) {
  if (!query) return null

  return (
    <div className="autocomplete">
      {loading && <p className="hint">Buscando sugerencias...</p>}
      {!loading && items.length === 0 && <p className="hint">Sin sugerencias.</p>}
      {!loading && items.map((item, index) => {
        const isActive = index === highlightedIndex
        const className = `suggestion-row ${isActive ? 'active' : ''}`

        if (item.type === 'set') {
          return (
            <button key={`${item.type}-${item.id}`} className={className} type="button" onMouseEnter={() => onHover(index)} onClick={() => onSelect(item)}>
              <Suggestion item={item} />
            </button>
          )
        }

        return (
          <button key={`${item.type}-${item.id}`} className={className} type="button" onMouseEnter={() => onHover(index)} onClick={() => onSelect(item)}>
            <Suggestion item={item} />
          </button>
        )
      })}
    </div>
  )
}
