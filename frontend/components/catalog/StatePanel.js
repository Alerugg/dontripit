import './StatePanel.css'

export default function StatePanel({ title, description, error = false, tone = 'default', loading = false }) {
  return (
    <section
      className={`state-panel-v2 panel state-panel-${tone} ${error ? 'is-error' : ''} ${loading ? 'is-loading' : ''}`}
      role={loading ? 'status' : undefined}
      aria-live={loading ? 'polite' : undefined}
    >
      {loading ? <span className="dri-loading-spinner" aria-hidden="true" /> : null}
      <div>
        <h3>{title}</h3>
        <p>{description}</p>
      </div>
    </section>
  )
}
