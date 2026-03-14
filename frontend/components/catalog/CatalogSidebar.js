export default function CatalogSidebar({
  query,
  onQueryChange,
  game,
  onGameChange,
  type,
  onTypeChange,
  view,
  onViewChange,
  gameOptions,
  typeOptions,
}) {
  return (
    <aside className="catalog-sidebar panel">
      <div className="filter-group">
        <label className="filter-label">Buscar cartas / prints / sets</label>
        <input
          className="input"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="Ej: pikachu, dark magician, black lotus"
        />
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
