import { redirect } from 'next/navigation'

export default async function LegacyPlayPage({ params }) {
  const { slug } = await params
  redirect(`/games/${slug}`)
}
