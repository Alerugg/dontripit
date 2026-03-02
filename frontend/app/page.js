import Link from 'next/link'

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 p-8">
      <h1 className="text-3xl font-bold">Next.js + Flask</h1>
      <p className="text-center">
        Backend health endpoint:{' '}
        <Link href="/api/health" className="underline">
          /api/health
        </Link>
      </p>
      <p className="text-center">
        Python endpoint:{' '}
        <Link href="/api/python" className="underline">
          /api/python
        </Link>
      </p>
      <p className="text-sm opacity-70">Backend source: backend/app.py</p>
    </main>
  )
}
