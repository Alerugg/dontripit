'use client'

export default function SearchControls({
  query,
  onQueryChange,
  selectedGame,
  onGameChange,
  selectedType,
  onTypeChange,
  games,
}) {
  return (
    <section className="panel controls-panel">
      <div className="search-row">
        <input
          className="search-input"
          type="text"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="Busca por nombre, set code o collector number..."
        />
      </div>

      <div className="filters-row">
        <label>
          Juego
          <select value={selectedGame} onChange={(event) => onGameChange(event.target.value)}>
            <option value="">Todos los juegos</option>
            {games.map((game) => (
              <option key={game.slug} value={game.slug}>{game.name}</option>
            ))}
          </select>
        </label>

        <label>
          Tipo
          <select value={selectedType} onChange={(event) => onTypeChange(event.target.value)}>
            <option value="">Todos</option>
            <option value="card">card</option>
            <option value="print">print</option>
            <option value="set">set</option>
          </select>
        </label>
      </div>
    </section>
  )
}
