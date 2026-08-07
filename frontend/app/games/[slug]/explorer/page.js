import { redirect } from 'next/navigation'

export default async function LegacyGameExplorerPage({ params }) {
  const { slug } = await params
  redirect(`/games/${slug}`)
}
