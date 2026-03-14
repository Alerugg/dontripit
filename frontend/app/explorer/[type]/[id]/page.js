import { redirect } from 'next/navigation'

export default function LegacyExplorerDetailPage({ params }) {
  if (params.type === 'print') {
    redirect(`/prints/${params.id}`)
  }

  redirect(`/cards/${params.id}`)
}
