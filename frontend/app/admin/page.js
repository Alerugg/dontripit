'use client'

import { useMemo, useState } from 'react'

export default function AdminPage() {
  const [apiKey, setApiKey] = useState('')
  const [form, setForm] = useState({
    pokemon_set: '',
    pokemon_limit: '200',
    mtg_limit: '200',
    yugioh_limit: '200',
    riftbound_limit: '200',
    incremental: true,
  })
  const [statusLoading, setStatusLoading] = useState(false)
  const [refreshLoading, setRefreshLoading] = useState(false)
  const [statusPayload, setStatusPayload] = useState(null)
  const [refreshPayload, setRefreshPayload] = useState(null)
  const [error, setError] = useState('')

  const headers = useMemo(() => (apiKey.trim() ? { 'X-API-Key': apiKey.trim() } : {}), [apiKey])

  async function loadStatus() {
    setStatusLoading(true)
    setError('')
    try {
      const response = await fetch('/api/admin/ingest-status', { headers })
      const payload = await response.json()
      if (!response.ok) {
        throw new Error(payload?.error || `HTTP ${response.status}`)
      }
      setStatusPayload(payload)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setStatusLoading(false)
    }
  }

  async function runRefresh() {
    setRefreshLoading(true)
    setError('')
    try {
      const body = {
        pokemon_set: form.pokemon_set.trim() || null,
        pokemon_limit: Number(form.pokemon_limit) || 200,
        mtg_limit: Number(form.mtg_limit) || 200,
        yugioh_limit: Number(form.yugioh_limit) || 200,
        riftbound_limit: Number(form.riftbound_limit) || 200,
        incremental: Boolean(form.incremental),
      }
      const response = await fetch('/api/admin/refresh', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...headers,
        },
        body: JSON.stringify(body),
      })
      const payload = await response.json()
      if (!response.ok) {
        throw new Error(payload?.error || `HTTP ${response.status}`)
      }
      setRefreshPayload(payload)
      await loadStatus()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setRefreshLoading(false)
    }
  }

  function onChange(name, value) {
    setForm((prev) => ({ ...prev, [name]: value }))
  }

  return (
    <main className="mx-auto min-h-screen max-w-6xl p-6 text-black">
      <h1 className="mb-4 text-3xl font-bold">Admin Ingest</h1>

      <div className="mb-4 rounded border bg-white p-4">
        <label className="mb-2 block text-sm font-medium">X-API-Key (admin)</label>
        <input className="w-full rounded border px-3 py-2" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
      </div>

      <div className="mb-4 rounded border bg-white p-4">
        <h2 className="mb-3 text-xl font-semibold">Run refresh</h2>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <input className="rounded border px-3 py-2" placeholder="pokemon_set (base1)" value={form.pokemon_set} onChange={(e) => onChange('pokemon_set', e.target.value)} />
          <input className="rounded border px-3 py-2" placeholder="pokemon_limit" value={form.pokemon_limit} onChange={(e) => onChange('pokemon_limit', e.target.value)} />
          <input className="rounded border px-3 py-2" placeholder="mtg_limit" value={form.mtg_limit} onChange={(e) => onChange('mtg_limit', e.target.value)} />
          <input className="rounded border px-3 py-2" placeholder="yugioh_limit" value={form.yugioh_limit} onChange={(e) => onChange('yugioh_limit', e.target.value)} />
          <input className="rounded border px-3 py-2" placeholder="riftbound_limit" value={form.riftbound_limit} onChange={(e) => onChange('riftbound_limit', e.target.value)} />
          <label className="flex items-center gap-2 rounded border px-3 py-2">
            <input type="checkbox" checked={form.incremental} onChange={(e) => onChange('incremental', e.target.checked)} /> incremental
          </label>
        </div>
        <div className="mt-3 flex gap-2">
          <button className="rounded bg-black px-4 py-2 text-white" onClick={runRefresh} disabled={refreshLoading}>
            {refreshLoading ? 'Running...' : 'Run refresh'}
          </button>
          <button className="rounded border px-4 py-2" onClick={loadStatus} disabled={statusLoading}>
            {statusLoading ? 'Loading...' : 'Reload status'}
          </button>
        </div>
      </div>

      {error ? <p className="mb-4 text-red-700">{error}</p> : null}

      {statusPayload ? (
        <section className="mb-4 rounded border bg-white p-4">
          <h2 className="mb-3 text-xl font-semibold">Ingest status</h2>
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr>
                <th className="border p-2 text-left">Source</th>
                <th className="border p-2 text-left">Records</th>
                <th className="border p-2 text-left">Last run</th>
              </tr>
            </thead>
            <tbody>
              {(statusPayload.sources || []).map((row) => (
                <tr key={row.name}>
                  <td className="border p-2">{row.name}</td>
                  <td className="border p-2">{row.records}</td>
                  <td className="border p-2">{row.last_run_at || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {refreshPayload ? (
        <section className="rounded border bg-white p-4">
          <h2 className="mb-2 text-xl font-semibold">Refresh result</h2>
          <pre className="overflow-x-auto rounded bg-gray-100 p-3 text-xs">{JSON.stringify(refreshPayload, null, 2)}</pre>
        </section>
      ) : null}
    </main>
  )
}
