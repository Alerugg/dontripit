export default function CatalogSidebar({
  inputValue,
  onInputChange,
  onSubmitSearch,
  suggestions,
  suggestionsLoading,
  onSuggestionSelect,
  game,
  onGameChange,
  type,
  onTypeChange,
  view,
  onViewChange,
  gameOptions,
  typeOptions,
}) {
  const hasSuggestions = suggestionsLoading || suggestions.length > 0

  return (
    <aside className="catalog-sidebar panel">
      <div className="filter-group">
        <label className="filter-label">Buscar cartas / prints / sets</label>
        <form
          className="search-form"
          onSubmit={(event) => {
            event.preventDefault()
            onSubmitSearch()
          }}
        >
          <div className="search-input-wrap">
            <input
              className="input"
              value={inputValue}
              onChange={(event) => onInputChange(event.target.value)}
              placeholder="Ej: pikachu, kai'sa, dark magician"
            />
            {hasSuggestions && (
              <div className="suggestions-dropdown panel">
                {suggestionsLoading && <p className="suggestion-hint">Buscando sugerencias...</p>}
                {!suggestionsLoading && suggestions.map((item) => (
                  <button
                    key={`${item.type}-${item.id}`}
                    className="suggestion-item"
                    type="button"
                    onClick={() => onSuggestionSelect(item)}
                  >
                    <strong>{item.title || item.name || 'Sin título'}</strong>
                    <small>
                      {item.type || 'item'}
                      {item.game ? ` · ${item.game}` : ''}
                      {item.set_code ? ` · ${item.set_code}` : ''}
                      {item.collector_number ? ` · #${item.collector_number}` : ''}
                    </small>
                  </button>
                ))}
              </div>
            )}
          </div>
          <button type="submit" className="primary-btn">Buscar</button>
        </form>
      </div>

      <div className="filter-group">
        <label className="filter-label">Juego</label>
        <select className="input" value={game} onChange={(event) => onGameChange(event.target.value)}>
          {gameOptions.map((option) => (
            <option key={option.value || 'all'} value={option.value}>{option.label}</option>
          ))}
        </select>
      </div>

      <div className="filter-group">
        <label className="filter-label">Tipo de resultado</label>
        <select className="input" value={type} onChange={(event) => onTypeChange(event.target.value)}>
          {typeOptions.map((option) => (
            <option key={option.value || 'all'} value={option.value}>{option.label}</option>
          ))}
        </select>
      </div>

      <div className="filter-group">
        <label className="filter-label">Vista</label>
        <div className="segmented">
          <button className={view === 'grid' ? 'segmented-active' : ''} onClick={() => onViewChange('grid')}>Grid</button>
          <button className={view === 'list' ? 'segmented-active' : ''} onClick={() => onViewChange('list')}>Lista</button>
        </div>
      </div>

      <div className="filter-group muted-block">
        <p className="filter-label">Próximos filtros</p>
        <ul>
          <li>Set / Collector Number</li>
          <li>Rarity / Variant / Foil</li>
          <li>Tengo / Me falta</li>
          <li>Wishlist / Marketplace</li>
        </ul>
      </div>
    </aside>
  )
}
