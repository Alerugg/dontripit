import { redirect } from 'next/navigation'

export default function LegacyGameExplorerPage({ params }) {
  redirect(`/games/${params.slug}`)
}
