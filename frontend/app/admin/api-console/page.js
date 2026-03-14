'use client'

import { useMemo, useState } from 'react'
import TopNav from '../../../components/layout/TopNav'

const PRESETS = [
  { key: 'search', label: 'Search', path: '/api/catalog/search', params: { q: 'p', game: 'pokemon', type: '', limit: 20, offset: 0 } },
  { key: 'card', label: 'Card by ID', path: '/api/catalog/cards/{id}', params: { id: '' } },
  { key: 'print', label: 'Print by ID', path: '/api/catalog/prints/{id}', params: { id: '' } },
]

function buildUrl(preset, params) {
  let url = preset.path

  if (url.includes('{id}')) {
    url = url.replace('{id}', params.id || '')
    return url
  }

  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    query.set(key, String(value))
  })

  const q = query.toString()
  return q ? `${url}?${q}` : url
}

export default function ApiConsolePage() {
  const [presetKey, setPresetKey] = useState('search')
  const preset = useMemo(() => PRESETS.find((item) => item.key === presetKey) || PRESETS[0], [presetKey])

  const [params, setParams] = useState(preset.params)
  const [response, setResponse] = useState(null)
  const [loading, setLoading] = useState(false)

  function switchPreset(nextKey) {
    setPresetKey(nextKey)
    const nextPreset = PRESETS.find((item) => item.key === nextKey)
    setParams(nextPreset.params)
    setResponse(null)
  }

  function updateParam(name, value) {
    setParams((prev) => ({ ...prev, [name]: value }))
  }

  async function runRequest(event) {
    event.preventDefault()
    const targetUrl = buildUrl(preset, params)

    setLoading(true)
    try {
      const res = await fetch(targetUrl, { cache: 'no-store' })
      const payload = await res.json().catch(() => ({ error: 'invalid_json' }))
      setResponse({ ok: res.ok, status: res.status, payload, targetUrl })
    } catch (error) {
      setResponse({ ok: false, status: 0, payload: { error: error.message }, targetUrl })
    } finally {
      setLoading(false)
    }
  }

  return (
    <main>
      <TopNav />
      <section className="admin-shell">
        <header className="panel admin-header">
          <p className="kicker">Zona privada / internal tooling</p>
          <h1>Admin API Console</h1>
          <p>Utilidad interna para inspeccionar el BFF de catálogo sin mezclarse con la UX pública.</p>
        </header>

        <div className="admin-grid">
          <form className="panel admin-form" onSubmit={runRequest}>
            <label className="filter-label">Endpoint preset</label>
            <select className="input" value={presetKey} onChange={(event) => switchPreset(event.target.value)}>
              {PRESETS.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
            </select>

            <div className="param-grid">
              {Object.keys(params).map((paramName) => (
                <label className="filter-label" key={paramName}>
                  {paramName}
                  <input
                    className="input"
                    value={params[paramName]}
                    onChange={(event) => updateParam(paramName, event.target.value)}
                  />
                </label>
              ))}
            </div>

            <p className="request-preview">{buildUrl(preset, params)}</p>
            <button className="primary-btn" type="submit" disabled={loading}>{loading ? 'Ejecutando...' : 'Ejecutar request'}</button>
          </form>

          <section className="panel admin-response">
            <h2>Response Viewer</h2>
            {!response && <p className="meta-subtitle">Aún no hay requests ejecutados.</p>}
            {response && (
              <>
                <p><strong>Status:</strong> {response.status} · {response.ok ? 'OK' : 'Error'}</p>
                <p><strong>URL:</strong> {response.targetUrl}</p>
                <pre>{JSON.stringify(response.payload, null, 2)}</pre>
              </>
            )}
          </section>
        </div>
      </section>
    </main>
  )
}
