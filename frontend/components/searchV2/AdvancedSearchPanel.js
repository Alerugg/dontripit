'use client'

import { useEffect, useMemo, useState } from 'react'
import { fetchFacetValuesV2 } from '../../lib/searchV2/client'
import './SearchV2.css'

const QUICK_FILTER_KEYS_BY_GAME = {
  onepiece: new Set(['color', 'card_type', 'promo', 'sp', 'treasure_rare']),
  pokemon: new Set(['types', 'stage', 'rarity', 'regulation_mark', 'finish', 'stamp']),
  yugioh: new Set(['set', 'release', 'card_class', 'attribute', 'archetype', 'rarity']),
  magic: new Set(['set', 'color_identity', 'card_type', 'rarity', 'finish']),
}

const QUICK_FILTER_COPY_BY_GAME = {
  onepiece: 'Color, tipo o hit especial.',
  pokemon: 'Tipo, rareza, regulación o acabado.',
  yugioh: 'Set, clase, atributo o rareza.',
  magic: 'Set, color, tipo, rareza o finish.',
}

function isEmpty(value) {
  if (value === undefined || value === null || value === '') return true
  if (Array.isArray(value)) return value.length === 0
  if (typeof value === 'object') return Object.values(value).every((item) => item === '' || item === null || item === undefined)
  return false
}

function formatActiveValue(value) {
  if (value === true) return 'Sí'
  if (Array.isArray(value)) return value.join(', ')
  if (typeof value === 'object' && value) {
    const hasMin = value.min !== undefined && value.min !== null && value.min !== ''
    const hasMax = value.max !== undefined && value.max !== null && value.max !== ''
    if (hasMin && hasMax) return `${value.min}–${value.max}`
    if (hasMin) return `≥ ${value.min}`
    if (hasMax) return `≤ ${value.max}`
  }
  return String(value)
}

function DynamicFacetPicker({ gameSlug, facet, value, onChange }) {
  const [input, setInput] = useState('')
  const [options, setOptions] = useState([])
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const current = facet.multi_value ? (Array.isArray(value) ? value : value ? [value] : []) : value

  useEffect(() => {
    if (!open) return undefined
    let cancelled = false
    const handle = setTimeout(async () => {
      setLoading(true)
      try {
        const rows = await fetchFacetValuesV2({ game: gameSlug, key: facet.key, q: input, limit: 24 })
        if (!cancelled) setOptions(rows)
      } catch {
        if (!cancelled) setOptions([])
      } finally {
        if (!cancelled) setLoading(false)
      }
    }, input ? 140 : 0)
    return () => {
      cancelled = true
      clearTimeout(handle)
    }
  }, [facet.key, gameSlug, input, open])

  function choose(option) {
    const nextValue = option.value
    if (facet.multi_value) {
      onChange(current.includes(nextValue) ? current : [...current, nextValue])
    } else {
      onChange(nextValue)
      setOpen(false)
    }
    setInput('')
  }

  function remove(selected) {
    if (facet.multi_value) {
      const next = current.filter((item) => item !== selected)
      onChange(next.length ? next : undefined)
    } else {
      onChange(undefined)
    }
  }

  return (
    <div className="sv2-picker">
      {facet.multi_value && current.length > 0 ? (
        <div className="sv2-picker-selected">
          {current.map((selected) => (
            <button key={selected} type="button" onClick={() => remove(selected)}>{selected} ×</button>
          ))}
        </div>
      ) : null}
      {!facet.multi_value && current ? (
        <button type="button" className="sv2-picker-single" onClick={() => remove(current)}>{current} ×</button>
      ) : null}
      <div className="sv2-picker-input-wrap">
        <input
          type="text"
          className="sv2-facet-input"
          value={input}
          onFocus={() => setOpen(true)}
          onChange={(event) => {
            setInput(event.target.value)
            setOpen(true)
          }}
          onKeyDown={(event) => {
            if (event.key === 'Escape') setOpen(false)
            if (event.key === 'Enter' && options[0]) {
              event.preventDefault()
              choose(options[0])
            }
          }}
          placeholder={`Buscar ${facet.label.toLowerCase()}…`}
          autoComplete="off"
        />
        {open ? (
          <div className="sv2-picker-menu">
            {loading ? <div className="sv2-picker-state">Buscando…</div> : null}
            {!loading && options.length === 0 ? <div className="sv2-picker-state">Sin coincidencias</div> : null}
            {!loading && options.map((option) => (
              <button
                type="button"
                key={`${option.value}-${option.label}`}
                className="sv2-picker-option"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(option)}
              >
                <span>
                  <strong>{option.value}</strong>
                  {option.label && option.label !== option.value ? <small>{option.label}</small> : null}
                </span>
                <em>{Number(option.count || 0).toLocaleString()}</em>
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  )
}

function FacetControl({ gameSlug, facet, value, onChange }) {
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

  if ((facet.ui_type === 'chips' || facet.ui_type === 'multi_select') && options.length > 0) {
    const current = facet.multi_value ? (Array.isArray(value) ? value : value ? [value] : []) : value
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

  if (facet.ui_type === 'autocomplete' || facet.ui_type === 'multi_select' || facet.searchable) {
    return <DynamicFacetPicker gameSlug={gameSlug} facet={facet} value={value} onChange={onChange} />
  }

  return (
    <input
      type="text"
      className="sv2-facet-input"
      value={value || ''}
      onChange={(event) => onChange(event.target.value || undefined)}
      placeholder={facet.label}
    />
  )
}

export default function AdvancedSearchPanel({
  gameSlug,
  groups = {},
  values = {},
  onChange,
  onSearch,
  onReset,
  loading = false,
  open = false,
  onToggle,
}) {
  const quickFilterKeys = QUICK_FILTER_KEYS_BY_GAME[gameSlug] || new Set()
  const quickFilterCopy = QUICK_FILTER_COPY_BY_GAME[gameSlug] || 'Elige solo lo que necesites.'

  const facetByKey = useMemo(() => {
    const entries = Object.values(groups).flat().map((facet) => [facet.key, facet])
    return Object.fromEntries(entries)
  }, [groups])

  const activeEntries = Object.entries(values).filter(([, value]) => !isEmpty(value))
  const quickFacets = useMemo(
    () => Object.values(groups)
      .flat()
      .filter((facet) => facet.quick_filter && quickFilterKeys.has(facet.key))
      .sort((a, b) => Number(a.display_order || 0) - Number(b.display_order || 0)),
    [groups, quickFilterKeys],
  )
  const fullGroups = useMemo(() => Object.fromEntries(
    Object.entries(groups)
      .map(([groupName, facets]) => [groupName, facets.filter((facet) => !quickFilterKeys.has(facet.key))])
      .filter(([, facets]) => facets.length > 0),
  ), [groups, quickFilterKeys])

  return (
    <section className={`sv2-advanced ${open ? 'is-open' : ''}`}>
      <div className="sv2-advanced-head">
        <div>
          <p className="eyebrow">Opcional</p>
          <h3>¿Buscas una versión concreta?</h3>
          <p>Set, idioma, rareza, acabado y otros detalles están aquí cuando los necesites.</p>
        </div>
        <button type="button" className="sv2-advanced-toggle" onClick={onToggle} aria-expanded={open}>
          {open ? 'Cerrar filtros' : 'Afinar búsqueda'}
          <span aria-hidden="true">{open ? '−' : '+'}</span>
        </button>
      </div>

      {quickFacets.length > 0 ? (
        <div className="sv2-quick-filters">
          <div className="sv2-quick-heading">
            <div>
              <strong>Atajos útiles</strong>
              <span>{quickFilterCopy}</span>
            </div>
            <button type="button" className="sv2-quick-apply" onClick={onSearch} disabled={loading}>
              {loading ? 'Buscando…' : 'Ver resultados'}
            </button>
          </div>
          <div className="sv2-quick-grid">
            {quickFacets.map((facet) => (
              <div key={`quick-${facet.key}`} className={`sv2-quick-facet sv2-quick-${facet.key}`}>
                {facet.ui_type !== 'toggle' ? <span>{facet.label}</span> : null}
                <FacetControl
                  gameSlug={gameSlug}
                  facet={facet}
                  value={values[facet.key]}
                  onChange={(nextValue) => onChange(facet.key, nextValue)}
                />
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {activeEntries.length > 0 ? (
        <div className="sv2-active-row">
          <span className="sv2-active-label">Filtros elegidos</span>
          {activeEntries.map(([key, value]) => (
            <button key={key} type="button" className="sv2-active-filter" onClick={() => onChange(key, undefined)}>
              {facetByKey[key]?.label || key}: {formatActiveValue(value)} ×
            </button>
          ))}
        </div>
      ) : null}

      {open ? (
        <>
          <div className="sv2-facet-groups">
            {Object.entries(fullGroups).map(([groupName, facets]) => (
              <fieldset key={groupName} className="sv2-facet-group">
                <legend>{groupName}</legend>
                <div className="sv2-facet-grid">
                  {facets.map((facet) => (
                    <div key={`${facet.scope}-${facet.key}`} className={`sv2-facet sv2-facet-${facet.ui_type}`}>
                      {facet.ui_type !== 'toggle' ? <span className="sv2-facet-label">{facet.label}</span> : null}
                      <FacetControl
                        gameSlug={gameSlug}
                        facet={facet}
                        value={values[facet.key]}
                        onChange={(nextValue) => onChange(facet.key, nextValue)}
                      />
                    </div>
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
              {loading ? 'Buscando…' : 'Aplicar filtros'}
            </button>
          </div>
        </>
      ) : null}
    </section>
  )
}
