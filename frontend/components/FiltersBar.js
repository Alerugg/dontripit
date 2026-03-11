'use client'

export default function FiltersBar({ games, game, onGameChange, resultType, onResultTypeChange }) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <label className="text-sm font-medium text-slate-700">Juego
        <select value={game} onChange={(event) => onGameChange(event.target.value)} className="mt-1 w-full rounded-xl border border-slate-300 bg-white px-3 py-2">
          <option value="">Todos</option>
          {games.map((g) => <option key={g.slug} value={g.slug}>{g.name}</option>)}
        </select>
      </label>

      <label className="text-sm font-medium text-slate-700">Tipo
        <select value={resultType} onChange={(event) => onResultTypeChange(event.target.value)} className="mt-1 w-full rounded-xl border border-slate-300 bg-white px-3 py-2">
          <option value="">Todos</option>
          <option value="card">Card</option>
          <option value="print">Print</option>
          <option value="set">Set</option>
        </select>
      </label>
    </div>
  )
}
