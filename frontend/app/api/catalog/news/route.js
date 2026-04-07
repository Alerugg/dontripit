import { NextResponse } from 'next/server'
import { normalizeGameSlug } from '../../../../lib/catalog/games'

const FALLBACK_NEWS = {
  pokemon: [
    {
      title: 'Noticias oficiales de Pokémon TCG',
      excerpt: 'Revisa anuncios de expansiones, promos y eventos oficiales desde la fuente principal.',
      source: 'Pokemon.com',
      tag: 'Oficial',
      href: 'https://www.pokemon.com/us/pokemon-news',
    },
  ],
  onepiece: [
    {
      title: 'Noticias oficiales de One Piece Card Game',
      excerpt: 'Consulta comunicados, reglas y lanzamientos en el portal oficial de Bandai.',
      source: 'One Piece Card Game',
      tag: 'Oficial',
      href: 'https://en.onepiece-cardgame.com/news/',
    },
  ],
  magic: [
    {
      title: 'Novedades de Magic: The Gathering',
      excerpt: 'Cobertura oficial de productos, previews y cambios de juego.',
      source: 'magic.wizards.com',
      tag: 'Oficial',
      href: 'https://magic.wizards.com/en/news',
    },
  ],
}

const DEFAULT_NEWS = [
  {
    title: 'Novedades del catálogo TCG',
    excerpt: 'Mientras se conecta un proveedor dinámico, revisa las fuentes oficiales por juego.',
    source: 'Dontripit Hub',
    tag: 'Fallback',
    href: '/hubs',
  },
]

export async function GET(request) {
  const { searchParams } = new URL(request.url)
  const game = normalizeGameSlug((searchParams.get('game') || '').trim().toLowerCase())
  const limit = Math.min(12, Math.max(1, Number(searchParams.get('limit') || 6)))
  const items = (FALLBACK_NEWS[game] || DEFAULT_NEWS).slice(0, limit)

  return NextResponse.json({
    items,
    provider: 'fallback_static',
    pending_provider: true,
  })
}
