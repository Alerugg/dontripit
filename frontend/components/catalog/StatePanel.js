import './StatePanel.css'

export default function StatePanel({ title, description, error = false, tone = 'default' }) {
  return (
    <section className={`state-panel-v2 state-panel-${tone} ${error ? 'is-error' : ''}`}>
      <h3>{title}</h3>
      <p>{description}</p>
    </section>
  )
}