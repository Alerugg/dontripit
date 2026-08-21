'use client'

import { useState } from 'react'

const STEPS = [
  {
    key: 'card',
    step: '01',
    label: 'Carta canónica',
    eyebrow: 'QUÉ CARTA ES',
    title: 'La identidad que reconoces',
    copy: 'Nombre, número, set y rareza. Aquí todavía no existe un precio universal.',
  },
  {
    key: 'print',
    step: '02',
    label: 'Impresión física',
    eyebrow: 'QUÉ OBJETO ES',
    title: 'La versión que realmente existe',
    copy: 'Idioma, acabado, variante y región convierten la carta en un objeto físico exacto.',
  },
  {
    key: 'market',
    step: '03',
    label: 'Mercado',
    eyebrow: 'QUÉ PODEMOS MOSTRAR',
    title: 'El mercado de esa impresión',
    copy: 'Solo aparece cuando existe una correspondencia exacta y verificable con la fuente.',
  },
]

function CardCanvas() {
  return (
    <div className="v16-story-canvas-inner v16-story-card-canvas">
      <div className="v16-card-silhouette" aria-hidden="true">
        <span />
        <i />
        <b />
      </div>
      <p>Una identidad lógica. Varias posibles impresiones.</p>
    </div>
  )
}

function PrintCanvas() {
  const rows = [
    ['EN', 'Holo', 'Estándar'],
    ['ES', 'Reverse', 'Estándar'],
    ['JP', 'Normal', 'Paralela'],
  ]
  return (
    <div className="v16-story-canvas-inner v16-story-print-canvas">
      {rows.map(([lang, finish, variant], index) => (
        <div key={lang} className={index === 1 ? 'is-selected' : ''} style={{ '--v16-indent': `${index * 8}px` }}>
          <span>{lang}</span>
          <strong>{finish}</strong>
          <small>{variant}</small>
        </div>
      ))}
      <p>Misma carta. Objetos físicos distintos.</p>
    </div>
  )
}

function MarketCanvas() {
  return (
    <div className="v16-story-canvas-inner v16-story-market-canvas">
      <div className="is-safe"><i /> <strong>Correspondencia exacta</strong><span>Mercado visible con fuente y fecha.</span></div>
      <div className="is-review"><i /> <strong>En revisión</strong><span>No publicamos un valor mientras falte certeza.</span></div>
      <div><i /> <strong>Sin precio seguro</strong><span>El vacío es preferible a una estimación.</span></div>
    </div>
  )
}

export default function HomeIdentityStoryV3() {
  const [active, setActive] = useState('print')
  const selected = STEPS.find((step) => step.key === active) || STEPS[1]

  return (
    <div className="v16-story-workspace">
      <div className="v16-story-tabs" role="tablist" aria-label="Cadena Carta, impresión y mercado">
        {STEPS.map((step) => {
          const isActive = step.key === active
          return (
            <button
              key={step.key}
              type="button"
              role="tab"
              aria-selected={isActive}
              className={isActive ? 'is-active' : ''}
              onClick={() => setActive(step.key)}
            >
              <span>{step.step}</span>
              <div>
                <small>{step.eyebrow}</small>
                <strong>{step.label}</strong>
                <p>{step.copy}</p>
              </div>
              <b aria-hidden="true">→</b>
            </button>
          )
        })}
      </div>

      <div className="v16-story-canvas" role="tabpanel" aria-live="polite">
        <header>
          <span>{selected.label}</span>
          <small>ESQUEMA</small>
        </header>
        <h3>{selected.title}</h3>
        <div className="v16-story-canvas-stage" key={active}>
          {active === 'card' && <CardCanvas />}
          {active === 'print' && <PrintCanvas />}
          {active === 'market' && <MarketCanvas />}
        </div>
      </div>
    </div>
  )
}
