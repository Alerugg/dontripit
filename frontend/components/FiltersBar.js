'use client'

export default function FiltersBar({ games, game, onGameChange, resultType, onResultTypeChange }) {
  return (
    <div className="filters-grid">
      <label>
        Juego
        <select value={game} onChange={(event) => onGameChange(event.target.value)}>
          <option value="">Todos</option>
          {games.map((row) => <option key={row.slug} value={row.slug}>{row.name}</option>)}
        </select>
      </label>
      <label>
        Tipo
        <select value={resultType} onChange={(event) => onResultTypeChange(event.target.value)}>
          <option value="">Todos</option>
          <option value="card">Card</option>
          <option value="print">Print</option>
          <option value="set">Set</option>
        </select>
      </label>
    </div>
  )
}
