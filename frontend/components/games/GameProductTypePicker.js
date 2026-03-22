'use client'

const PRODUCT_OPTIONS = [
  { value: 'singles', label: 'Cartas sueltas' },
  { value: 'packs', label: 'Sobres' },
  { value: 'booster-box', label: 'Booster boxes' },
  { value: 'premium-collection', label: 'Premium collection' },
  { value: 'limited-collection', label: 'Limited collection' },
  { value: 'championships-collection', label: 'Championships collection' },
  { value: 'cases', label: 'Cases' },
]

export default function GameProductTypePicker({ value, onChange }) {
  return (
    <section className="game-section panel-soft">
      <div className="section-heading compact">
        <p className="eyebrow">Tipo de producto</p>
        <h2>Explora por formato de producto.</h2>
      </div>
      <div className="chip-row">
        {PRODUCT_OPTIONS.map((option) => (
          <button
            key={option.value}
            type="button"
            className={`filter-chip ${value === option.value ? 'active' : ''}`}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </section>
  )
}
