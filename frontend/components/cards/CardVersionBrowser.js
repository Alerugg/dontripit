'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import FallbackImage from '../common/FallbackImage'
import { fetchCardVersions } from '../../lib/catalog/client'
import { getPrintHref } from '../../lib/catalog/routes'
import styles from './CardVersionBrowser.module.css'

const LANGUAGE_LABELS = {
  es: 'ES',
  en: 'EN',
  fr: 'FR',
  de: 'DE',
  it: 'IT',
  pt: 'PT',
  ja: 'JA',
  ko: 'KO',
  zh: 'ZH',
  zhs: 'ZH-S',
  zht: 'ZH-T',
}

function languageLabel(value) {
  const code = String(value || '').toLowerCase()
  return LANGUAGE_LABELS[code] || code.toUpperCase() || '—'
}

function versionSearchText(version) {
  return [
    version?.cardmarket?.name,
    version?.set_code,
    version?.set_name,
    version?.collector_number,
    version?.rarity,
    version?.variant,
    ...(version?.languages || []).map((item) => item.code),
  ].filter(Boolean).join(' ').toLowerCase()
}

function versionMeta(version) {
  return [
    version?.set_code ? String(version.set_code).toUpperCase() : null,
    version?.collector_number || null,
    version?.rarity || null,
    version?.is_foil ? 'Foil' : null,
    version?.variant && version.variant !== 'default' ? version.variant : null,
  ].filter(Boolean).join(' · ')
}

function firstPrintForLanguage(version, language) {
  const ids = (version?.languages || []).find((item) => item.code === language)?.print_ids || []
  if (!ids.length) return null
  return (version?.prints || []).find((print) => String(print.print_id) === String(ids[0])) || null
}

export default function CardVersionBrowser({ cardId, cardName, gameLabel }) {
  const [payload, setPayload] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [language, setLanguage] = useState('')

  useEffect(() => {
    if (!cardId) return undefined
    let cancelled = false
    setLoading(true)
    setError('')
    fetchCardVersions(cardId)
      .then((result) => {
        if (!cancelled) setPayload(result)
      })
      .catch((requestError) => {
        if (!cancelled) {
          setPayload(null)
          setError(requestError?.message || 'No pudimos cargar las versiones de esta carta.')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [cardId])

  const versions = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    return (payload?.versions || []).filter((version) => {
      if (language && !(version.languages || []).some((item) => item.code === language)) return false
      if (normalizedQuery && !versionSearchText(version).includes(normalizedQuery)) return false
      return true
    })
  }, [payload, query, language])

  if (loading) {
    return <section className="panel-soft"><p className="detail-meta">Organizando versiones e idiomas físicos…</p></section>
  }

  if (error) {
    return <section className="panel-soft"><p className="detail-meta">{error}</p></section>
  }

  const allLanguages = payload?.languages || []

  return (
    <section className={styles.browser}>
      <div className={`${styles.summary} panel-soft`}>
        <div className={styles.summaryCopy}>
          <p className="eyebrow">Versiones de la carta</p>
          <h2>Elige la edición correcta, luego el idioma.</h2>
          <p className="detail-meta">
            Don’tRipIt agrupa las impresiones que Cardmarket trata como la misma versión comercial. El idioma abre la carta física exacta; el botón de Cardmarket lleva al producto de mercado correspondiente.
          </p>
        </div>
        <div>
          <p className="detail-meta">Idiomas disponibles en {cardName || 'esta carta'}</p>
          <div className={styles.languageStrip}>
            <button
              type="button"
              onClick={() => setLanguage('')}
              className={`${styles.languageButton} ${!language ? styles.languageButtonActive : ''}`}
            >
              Todos
            </button>
            {allLanguages.map((code) => (
              <button
                key={code}
                type="button"
                onClick={() => setLanguage(code)}
                className={`${styles.languageButton} ${language === code ? styles.languageButtonActive : ''}`}
              >
                {languageLabel(code)}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className={styles.tools}>
        <input
          className={styles.search}
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Filtrar por set, código, rareza o versión…"
          aria-label="Filtrar versiones de esta carta"
        />
        <span className={styles.count}>{versions.length} de {payload?.version_count || 0} versiones</span>
      </div>

      <div className={styles.list}>
        {versions.map((version) => {
          const representative = version.representative_print || version.prints?.[0] || null
          const marketName = version.cardmarket?.name || version.set_name || version.set_code || 'Versión física'
          return (
            <article key={version.key} className={styles.version}>
              <div className={styles.media}>
                <FallbackImage
                  src={representative?.primary_image_url}
                  alt={`${cardName || 'Carta'} · ${marketName}`}
                  className="detail-image"
                  placeholderClassName="image-fallback"
                  label={version.set_code || gameLabel || 'TCG'}
                />
              </div>

              <div className={styles.versionCopy}>
                <div>
                  <p className="eyebrow">{version.market_status === 'linked' ? 'Versión Cardmarket' : 'Versión del catálogo'}</p>
                  <h3>{marketName}</h3>
                </div>
                <p className={styles.meta}>{versionMeta(version) || 'Edición física identificada'}</p>
                <div>
                  <p className={styles.meta}>Disponible en:</p>
                  <div className={styles.languages}>
                    {(version.languages || []).map((item) => {
                      const exactPrint = firstPrintForLanguage(version, item.code)
                      if (!exactPrint) return <span key={item.code} className={styles.languageChip}>{languageLabel(item.code)}</span>
                      return (
                        <Link
                          key={item.code}
                          href={getPrintHref(exactPrint.print_id)}
                          className={styles.languageChip}
                          title={`Abrir impresión exacta ${exactPrint.print_id}`}
                        >
                          {languageLabel(item.code)}{item.print_count > 1 ? ` · ${item.print_count}` : ''}
                        </Link>
                      )
                    })}
                  </div>
                </div>
              </div>

              <div className={styles.actions}>
                {version.cardmarket?.url ? (
                  <a
                    href={version.cardmarket.url}
                    target="_blank"
                    rel="noopener noreferrer sponsored"
                    className={styles.cardmarketButton}
                  >
                    Ver esta versión en Cardmarket ↗
                  </a>
                ) : (
                  <span className={styles.pendingButton}>Cardmarket pendiente</span>
                )}
                {version.cardmarket?.external_product_id ? (
                  <span className={styles.marketId}>Cardmarket #{version.cardmarket.external_product_id}</span>
                ) : null}
              </div>
            </article>
          )
        })}
      </div>

      {!versions.length ? (
        <div className={`${styles.empty} panel-soft`}>No hay versiones que coincidan con este filtro.</div>
      ) : null}
    </section>
  )
}
