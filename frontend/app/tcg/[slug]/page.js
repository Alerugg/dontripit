import { redirect } from 'next/navigation'

export default async function LegacyTcgPage({ params }) {
  const { slug } = await params
  redirect(`/games/${slug}`)
}
