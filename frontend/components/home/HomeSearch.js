'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'

const GAMES = [
  ['onepiece', 'One Piece'],
  ['pokemon', 'Pokémon'],
  ['magic', 'Magic'],
  ['yugioh', 'Yu‑Gi‑Oh!'],
]

export default function HomeSearch() {
  const router = useRouter()
  const [game, setGame] = useState('onepiece')
  const [query, setQuery] = useState('')

  function submit(event) {
    event.preventDefault()
    const clean = query.trim()
    router.push(`/games/${game}${clean ? `?q=${encodeURIComponent(clean)}` : ''}#buscar`)
  }

  return (
    <div className="v4-home-search-wrap">
      <div className="v4-game-tabs" role="group" aria-label="Juego para buscar">
        {GAMES.map(([slug, label]) => (
          <button
            key={slug}
            type="button"
            className={game === slug ? 'is-active' : ''}
            onClick={() => setGame(slug)}
          >
            {label}
          </button>
        ))}
      </div>
      <form className="v4-home-search" onSubmit={submit}>
        <span aria-hidden="true">⌕</span>
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={game === 'onepiece' ? 'Luffy, Zoro, OP05-119…' : 'Nombre, número o set…'}
          aria-label="Buscar en el catálogo"
        />
        <button type="submit" aria-label="Buscar">Buscar <b>→</b></button>
      </form>
    </div>
  )
}
