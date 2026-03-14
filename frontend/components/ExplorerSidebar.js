'use client'

export default function ExplorerSidebar({
  query,
  onQueryChange,
  selectedGame,
  onGameChange,
  selectedType,
  onTypeChange,
  games,
  viewMode,
  onViewModeChange,
}) {
  return (
    <aside className="sidebar panel">
      <h3>Filtros</h3>

      <label className="field-label">
        Búsqueda
        <input
          className="search-input"
          type="text"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="Nombre, set, collector..."
        />
      </label>

      <label className="field-label">
        Juego
        <select value={selectedGame} onChange={(event) => onGameChange(event.target.value)}>
          <option value="">Todos los juegos</option>
          {games.map((game) => (
            <option key={game.slug} value={game.slug}>{game.name}</option>
          ))}
        </select>
      </label>

      <label className="field-label">
        Tipo de resultado
        <select value={selectedType} onChange={(event) => onTypeChange(event.target.value)}>
          <option value="">Todos</option>
          <option value="card">Card</option>
          <option value="print">Print</option>
          <option value="set">Set</option>
        </select>
      </label>

      <div className="field-label">
        Vista
        <div className="view-mode-toggle">
          <button className={viewMode === 'grid' ? 'active' : ''} onClick={() => onViewModeChange('grid')}>Grid</button>
          <button className={viewMode === 'list' ? 'active' : ''} onClick={() => onViewModeChange('list')}>List</button>
        </div>
      </div>

      <div className="expansion-note">
        Preparado para expandir: rareza, set, idioma, estado de colección, binder y precios.
      </div>
    </aside>
  )
}
