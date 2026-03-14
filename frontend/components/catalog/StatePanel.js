export default function StatePanel({ title, description, error = false }) {
  return (
    <section className={`state-panel-v2 panel ${error ? 'is-error' : ''}`}>
      <h3>{title}</h3>
      <p>{description}</p>
    </section>
  )
}
