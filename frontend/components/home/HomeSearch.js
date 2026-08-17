'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'

const GAMES = [
  { slug: 'onepiece', label: 'One Piece', placeholder: 'Luffy, Zoro, OP05-119…', color: '#e8434c' },
  { slug: 'pokemon', label: 'Pokémon', placeholder: 'Pikachu, Charizard, 151…', color: '#ffcb05' },
  { slug: 'magic', label: 'Magic', placeholder: 'Sol Ring, Lightning Bolt, Commander…', color: '#ff7a45' },
  { slug: 'yugioh', label: 'Yu-Gi-Oh!', placeholder: 'Blue-Eyes, Dark Magician, LOB…', color: '#d9af3e' },
]

export default function HomeSearch() {
  const router = useRouter()
  const [game, setGame] = useState('onepiece')
  const [query, setQuery] = useState('')
  const activeGame = GAMES.find((item) => item.slug === game) || GAMES[0]

  function submit(event) {
    event.preventDefault()
    const clean = query.trim()
    router.push(`/games/${game}${clean ? `?q=${encodeURIComponent(clean)}` : ''}#buscar`)
  }

  return (
    <div className="v4-home-search-wrap" data-game={game} style={{ '--search-accent': activeGame.color }}>
      <div className="v4-game-tabs" role="group" aria-label="Juego para buscar">
        {GAMES.map((item) => (
          <button
            key={item.slug}
            type="button"
            className={game === item.slug ? 'is-active' : ''}
            aria-pressed={game === item.slug}
            onClick={() => setGame(item.slug)}
          >
            <i aria-hidden="true" style={{ background: item.color }} />
            {item.label}
          </button>
        ))}
      </div>
      <form className="v4-home-search" onSubmit={submit}>
        <span className="v4-search-icon" aria-hidden="true">⌕</span>
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={activeGame.placeholder}
          aria-label={`Buscar en ${activeGame.label}`}
          autoComplete="off"
        />
        <button type="submit" aria-label={`Buscar en ${activeGame.label}`}>Buscar <b aria-hidden="true">→</b></button>
      </form>
      <small className="v4-search-hint">Nombre, número o set · luego eliges la impresión exacta</small>
    </div>
  )
}
