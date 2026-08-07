'use client'

import './SearchV2.css'

function isEmpty(value) {
  if (value === undefined || value === null || value === '') return true
  if (Array.isArray(value)) return value.length === 0
  if (typeof value === 'object') return Object.values(value).every((item) => item === '' || item === null || item === undefined)
  return false
}

function normalizeTextValue(value, multiValue) {
  if (!multiValue) return value
  if (Array.isArray(value)) return value.join(', ')
  return value || ''
}

function parseTextValue(value, multiValue) {
  if (!multiValue) return value
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

function FacetControl({ facet, value, onChange }) {
  const options = Array.isArray(facet.options) ? facet.options : []

  if (facet.ui_type === 'toggle') {
    const active = value === true
    return (
      <button
        type="button"
        className={`sv2-toggle ${active ? 'is-active' : ''}`}
        onClick={() => onChange(active ? undefined : true)}
        aria-pressed={active}
      >
        <span className="sv2-toggle-dot" />
        {facet.label}
      </button>
    )
  }

  if (facet.ui_type === 'range') {
    const range = typeof value === 'object' && value ? value : {}
    return (
      <div className="sv2-range-row">
        <input
          type="number"
          inputMode="numeric"
          placeholder="Min"
          value={range.min ?? ''}
          onChange={(event) => onChange({ ...range, min: event.target.value === '' ? undefined : Number(event.target.value) })}
          aria-label={`${facet.label} mínimo`}
        />
        <span>—</span>
        <input
          type="number"
          inputMode="numeric"
          placeholder="Max"
          value={range.max ?? ''}
          onChange={(event) => onChange({ ...range, max: event.target.value === '' ? undefined : Number(event.target.value) })}
          aria-label={`${facet.label} máximo`}
        />
      </div>
    )
  }

  if (facet.ui_type === 'chips' && options.length > 0) {
    const current = facet.multi_value
      ? (Array.isArray(value) ? value : value ? [value] : [])
      : value
    return (
      <div className="sv2-chip-list">
        {options.map((option) => {
          const selected = facet.multi_value ? current.includes(option) : current === option
          return (
            <button
              key={option}
              type="button"
              className={`sv2-chip ${selected ? 'is-active' : ''}`}
              onClick={() => {
                if (facet.multi_value) {
                  const next = selected ? current.filter((item) => item !== option) : [...current, option]
                  onChange(next.length ? next : undefined)
                } else {
                  onChange(selected ? undefined : option)
                }
              }}
            >
              {option}
            </button>
          )
        })}
      </div>
    )
  }

  if ((facet.ui_type === 'multi_select' || facet.ui_type === 'chips') && options.length > 0) {
    const current = Array.isArray(value) ? value : value ? [value] : []
    return (
      <div className="sv2-chip-list">
        {options.map((option) => {
          const selected = current.includes(option)
          return (
            <button
              key={option}
              type="button"
              className={`sv2-chip ${selected ? 'is-active' : ''}`}
              onClick={() => {
                const next = selected ? current.filter((item) => item !== option) : [...current, option]
                onChange(next.length ? next : undefined)
              }}
            >
              {option}
            </button>
          )
        })}
      </div>
    )
  }

  return (
    <input
      type="text"
      className="sv2-facet-input"
      value={normalizeTextValue(value, facet.multi_value)}
      onChange={(event) => onChange(parseTextValue(event.target.value, facet.multi_value))}
      placeholder={facet.searchable ? `Buscar ${facet.label.toLowerCase()}…` : facet.label}
    />
  )
}

export default function AdvancedSearchPanel({
  groups = {},
  values = {},
  onChange,
  onSearch,
  onReset,
  loading = false,
  open = false,
  onToggle,
}) {
  const activeEntries = Object.entries(values).filter(([, value]) => !isEmpty(value))

  return (
    <section className={`sv2-advanced ${open ? 'is-open' : ''}`}>
      <div className="sv2-advanced-head">
        <div>
          <p className="eyebrow">Advanced Search</p>
          <h3>Encuentra la impresión exacta.</h3>
          <p>Combina identidad, características de carta y atributos de coleccionismo.</p>
        </div>
        <button type="button" className="sv2-advanced-toggle" onClick={onToggle}>
          {open ? 'Cerrar filtros' : 'Advanced Search'}
          <span aria-hidden="true">{open ? '−' : '+'}</span>
        </button>
      </div>

      {activeEntries.length > 0 && (
        <div className="sv2-active-row">
          <span className="sv2-active-label">Activos</span>
          {activeEntries.map(([key, value]) => (
            <button key={key} type="button" className="sv2-active-filter" onClick={() => onChange(key, undefined)}>
              {key}: {typeof value === 'object' ? JSON.stringify(value) : Array.isArray(value) ? value.join(', ') : String(value)} ×
            </button>
          ))}
        </div>
      )}

      {open && (
        <>
          <div className="sv2-facet-groups">
            {Object.entries(groups).map(([groupName, facets]) => (
              <fieldset key={groupName} className="sv2-facet-group">
                <legend>{groupName}</legend>
                <div className="sv2-facet-grid">
                  {facets.map((facet) => (
                    <label key={`${facet.scope}-${facet.key}`} className={`sv2-facet sv2-facet-${facet.ui_type}`}>
                      {facet.ui_type !== 'toggle' && (
                        <span className="sv2-facet-label">
                          {facet.label}
                          {facet.quick_filter ? <small>Quick</small> : null}
                        </span>
                      )}
                      <FacetControl
                        facet={facet}
                        value={values[facet.key]}
                        onChange={(nextValue) => onChange(facet.key, nextValue)}
                      />
                    </label>
                  ))}
                </div>
              </fieldset>
            ))}
          </div>

          <div className="sv2-advanced-actions">
            <button type="button" className="sv2-secondary-btn" onClick={onReset} disabled={loading || activeEntries.length === 0}>
              Limpiar
            </button>
            <button type="button" className="sv2-primary-btn" onClick={onSearch} disabled={loading}>
              {loading ? 'Filtrando…' : 'Buscar prints'}
            </button>
          </div>
        </>
      )}
    </section>
  )
}
