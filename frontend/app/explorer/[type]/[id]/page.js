import { redirect } from 'next/navigation'

export default async function LegacyExplorerDetailPage({ params }) {
  const { type, id } = await params

  if (type === 'print') {
    redirect(`/prints/${id}`)
  }

  redirect(`/cards/${id}`)
}
