import { redirect } from 'next/navigation'

export default function LegacyConsolePage() {
  redirect('/admin/api-console')
}
