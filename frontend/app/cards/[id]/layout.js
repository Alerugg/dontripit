import { callInternalApi } from '../../../lib/catalog/internalApi'
import { getGameConfig, normalizeGameSlug } from '../../../lib/catalog/games'
import { SITE_URL } from '../../../lib/site'

export async function generateMetadata({ params }) {
  const { id } = await params
  const upstream = await callInternalApi(`/api/v1/cards/${id}`)
  const card = upstream.ok ? upstream.payload : null
  const name = card?.name || `Carta ${id}`
  const gameSlug = normalizeGameSlug(card?.game_slug || card?.game || '')
  const gameLabel = getGameConfig(gameSlug)?.name || card?.game || 'TCG'
  const canonical = `${SITE_URL}/cards/${encodeURIComponent(id)}`
  const title = `${name} · ${gameLabel}`
  const socialTitle = `${title} · Don’tRipIt`
  const description = `Consulta ${name} en ${gameLabel}: versiones físicas, sets relacionados y referencias verificables de mercado en Don’tRipIt.`

  return {
    title,
    description,
    alternates: { canonical },
    openGraph: { title: socialTitle, description, url: canonical },
  }
}

export default function CardDetailLayoutRoute({ children }) {
  return children
}
