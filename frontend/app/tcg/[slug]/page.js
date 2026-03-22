import { redirect } from 'next/navigation'

export default function LegacyTcgPage({ params }) {
  redirect(`/games/${params.slug}`)
}
