import './StatePanel.css'

export default function StatePanel({ title, description, error = false, tone = 'default', loading = false }) {
  return (
    <section
      className={`state-panel-v2 panel state-panel-${tone} ${error ? 'is-error' : ''} ${loading ? 'is-loading' : ''}`}
      role={error ? 'alert' : loading ? 'status' : undefined}
      aria-live={error ? 'assertive' : loading ? 'polite' : undefined}
      aria-atomic={error || loading ? 'true' : undefined}
      aria-busy={loading ? 'true' : undefined}
    >
      {loading ? <span className="dri-loading-spinner" aria-hidden="true" /> : null}
      <div>
        <h3>{title}</h3>
        <p>{description}</p>
      </div>
    </section>
  )
}
