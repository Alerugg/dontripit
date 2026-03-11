'use client'

export default function ApiKeyPanel({
  adminToken,
  onAdminTokenChange,
  onGenerate,
  generating,
  apiKeyInput,
  onApiKeyInputChange,
  onSaveApiKey,
  onClearApiKey,
  status,
}) {
  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Autenticación API</h2>
        <p>Genera y guarda tu X-API-Key para desbloquear búsquedas, sugerencias y detalles.</p>
      </div>

      <div className="auth-grid">
        <label>
          Admin token
          <input type="password" value={adminToken} onChange={(event) => onAdminTokenChange(event.target.value)} placeholder="ADMIN_TOKEN" />
        </label>
        <button type="button" onClick={onGenerate} disabled={generating || !adminToken.trim()} className="primary-btn">
          {generating ? 'Generando...' : 'Generar API Key'}
        </button>
        <label>
          API Key activa
          <input type="password" value={apiKeyInput} onChange={(event) => onApiKeyInputChange(event.target.value)} placeholder="ak_..." />
        </label>
        <div className="auth-actions">
          <button type="button" onClick={onSaveApiKey} className="primary-btn">Guardar key</button>
          <button type="button" onClick={onClearApiKey} className="ghost-btn">Limpiar</button>
        </div>
      </div>

      <p className={`pill ${status.active ? 'pill-ok' : 'pill-warning'}`}>
        {status.active ? 'X-API-Key activa' : 'Sin X-API-Key, no se podrán cargar datos del catálogo'}
      </p>
    </section>
  )
}
