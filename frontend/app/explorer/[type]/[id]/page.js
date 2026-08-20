import { redirect } from 'next/navigation'

function explorerFallback(id, kind = '') {
  const search = new URLSearchParams()
  if (id) search.set('q', id)
  if (kind) search.set('kind', kind)
  search.set('view', 'grid')
  return `/explorer?${search.toString()}`
}

export default async function LegacyExplorerDetailPage({ params }) {
  const { type, id } = await params
  const entityType = String(type || '').trim().toLowerCase()
  const entityId = String(id || '').trim()

  if (entityType === 'print' || entityType === 'prints') {
    redirect(`/prints/${encodeURIComponent(entityId)}`)
  }

  if (entityType === 'card' || entityType === 'cards') {
    redirect(`/cards/${encodeURIComponent(entityId)}`)
  }

  if (entityType === 'set' || entityType === 'sets') {
    redirect(explorerFallback(entityId, 'set'))
  }

  redirect(explorerFallback(entityId))
}
