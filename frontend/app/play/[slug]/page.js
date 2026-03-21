import { redirect } from 'next/navigation'

export default function LegacyPlayPage({ params }) {
  redirect(`/games/${params.slug}/explorer`)
}
