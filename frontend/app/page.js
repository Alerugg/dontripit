import Link from 'next/link'

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen w-full max-w-4xl flex-col items-center justify-center gap-6 p-6 text-center">
      <h1 className="text-4xl font-bold text-slate-900">API-PROJECT Frontend</h1>
      <p className="text-slate-600">Explorador visual de cartas/prints consumiendo la API real de TCG.</p>
      <div className="flex flex-wrap justify-center gap-3">
        <Link href="/explorer" className="rounded-xl bg-slate-900 px-4 py-2 text-white">Abrir Explorer</Link>
        <Link href="/console" className="rounded-xl border border-slate-300 px-4 py-2">API Console</Link>
      </div>
    </main>
  )
}
