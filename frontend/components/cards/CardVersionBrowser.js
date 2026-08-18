'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import FallbackImage from '../common/FallbackImage'
import { fetchCardVersions } from '../../lib/catalog/client'
import { getPrintHref } from '../../lib/catalog/routes'
import styles from './CardVersionBrowser.module.css'

const LANGUAGE_LABELS = {
  es: 'ES', en: 'EN', fr: 'FR', de: 'DE', it: 'IT', pt: 'PT', ja: 'JA', ko: 'KO', zh: 'ZH', zhs: 'ZH-S', zht: 'ZH-T',
}

function languageLabel(value) {
  const code = String(value || '').toLowerCase()
  return LANGUAGE_LABELS[code] || code.toUpperCase() || '—'
}

function friendlyVariant(value) {
  const raw = String(value || '').trim()
  if (!raw || ['default', 'base'].includes(raw.toLowerCase())) return null
  if (/^rarity-/i.test(raw)) return null
  return raw.replace(/[-_]+/g, ' ')
}

function versionSearchText(version) {
  return [
    version?.cardmarket?.name,
    version?.set_code,
    version?.set_name,
    version?.collector_number,
    version?.rarity,
    friendlyVariant(version?.variant),
    ...(version?.languages || []).map((item) => item.code),
  ].filter(Boolean).join(' ').toLowerCase()
}

function versionMeta(version) {
  return [
    version?.collector_number || null,
    version?.rarity || null,
    version?.is_foil ? 'Foil' : null,
    friendlyVariant(version?.variant),
  ].filter(Boolean).join(' · ')
}

function printsForLanguage(version, language) {
  const ids = new Set(
    ((version?.languages || []).find((item) => item.code === language)?.print_ids || []).map(String),
  )
  return (version?.prints || []).filter((print) => ids.has(String(print.print_id)))
}

function exactPrintLabel(print) {
  return [
    print?.collector_number || null,
    print?.rarity || null,
    print?.is_foil ? 'Foil' : null,
    friendlyVariant(print?.variant),
  ].filter(Boolean).join(' · ')
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
      .then((result) => { if (!cancelled) setPayload(result) })
      .catch((requestError) => {
        if (!cancelled) {
          setPayload(null)
          setError(requestError?.message || 'No pudimos cargar las versiones de esta carta.')
        }
      })
      .finally(() => { if (!cancelled) setLoading(false) })
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

  if (loading) return <section className="panel-soft"><p className="detail-meta">Organizando versiones e idiomas físicos…</p></section>
  if (error) return <section className="panel-soft"><p className="detail-meta">{error}</p></section>

  const allLanguages = payload?.languages || []

  return (
    <section className={styles.browser}>
      <div className={styles.heading}>
        <div>
          <p className="eyebrow">Versiones disponibles</p>
          <h2>Elige la versión y el idioma</h2>
        </div>
        <span className={styles.count}>{versions.length} de {payload?.version_count || 0}</span>
      </div>

      <div className={styles.filters}>
        <input
          className={styles.search}
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Set, código, rareza o versión…"
          aria-label="Filtrar versiones de esta carta"
        />
        <div className={styles.languageStrip} aria-label={`Idiomas disponibles en ${cardName || 'esta carta'}`}>
          <button type="button" onClick={() => setLanguage('')} className={`${styles.languageButton} ${!language ? styles.languageButtonActive : ''}`}>Todos</button>
          {allLanguages.map((code) => (
            <button key={code} type="button" onClick={() => setLanguage(code)} className={`${styles.languageButton} ${language === code ? styles.languageButtonActive : ''}`}>
              {languageLabel(code)}
            </button>
          ))}
        </div>
      </div>

      <div className={styles.grid}>
        {versions.map((version) => {
          const representative = version.representative_print || version.prints?.[0] || null
          const versionTitle = version.set_name || version.set_code || version.cardmarket?.name || 'Versión física'
          const setCode = version.set_code ? String(version.set_code).toUpperCase() : null
          const representativeHref = representative?.print_id ? getPrintHref(representative.print_id) : null
          const image = (
            <FallbackImage
              src={representative?.primary_image_url}
              alt={`${cardName || 'Carta'} · ${versionTitle}`}
              className="detail-image"
              placeholderClassName="image-fallback"
              label={setCode || gameLabel || 'TCG'}
            />
          )

          return (
            <article key={version.key} className={styles.versionCard}>
              <div className={styles.mediaWrap}>
                {representativeHref ? (
                  <Link href={representativeHref} className={styles.mediaLink} aria-label={`Abrir ${versionTitle}`}>{image}</Link>
                ) : image}
                {version.market_status === 'linked' ? <span className={styles.marketBadge}>Cardmarket</span> : null}
              </div>

              <div className={styles.cardBody}>
                <div className={styles.titleBlock}>
                  {setCode ? <span className={styles.setCode}>{setCode}</span> : null}
                  <h3 title={versionTitle}>{versionTitle}</h3>
                  <p className={styles.meta}>{versionMeta(version) || 'Edición física identificada'}</p>
                </div>

                <div className={styles.languageBlock}>
                  <span className={styles.languageLabel}>Disponible en</span>
                  <div className={styles.languages}>
                    {(version.languages || []).map((item) => {
                      const exactPrints = printsForLanguage(version, item.code)
                      if (!exactPrints.length) return <span key={item.code} className={styles.languageChip}>{languageLabel(item.code)}</span>
                      if (exactPrints.length === 1) {
                        const exactPrint = exactPrints[0]
                        return (
                          <Link key={item.code} href={getPrintHref(exactPrint.print_id)} className={styles.languageChip} title={`Abrir ${languageLabel(item.code)}`}>
                            {languageLabel(item.code)}
                          </Link>
                        )
                      }
                      return (
                        <details key={item.code} className={styles.languageDetails}>
                          <summary className={styles.languageChip}>{languageLabel(item.code)} · {exactPrints.length}</summary>
                          <div className={styles.printChoices}>
                            {exactPrints.map((print) => (
                              <Link key={print.print_id} href={getPrintHref(print.print_id)} className={styles.printChoice}>
                                {exactPrintLabel(print) || `Versión ${languageLabel(item.code)}`}
                              </Link>
                            ))}
                          </div>
                        </details>
                      )
                    })}
                  </div>
                </div>

                <div className={styles.actions}>
                  {version.cardmarket?.url ? (
                    <a href={version.cardmarket.url} target="_blank" rel="noopener noreferrer sponsored" className={styles.cardmarketButton}>Ver en Cardmarket ↗</a>
                  ) : (
                    <span className={styles.pendingButton}>Cardmarket pendiente</span>
                  )}
                </div>
              </div>
            </article>
          )
        })}
      </div>

      {!versions.length ? <div className={`${styles.empty} panel-soft`}>No hay versiones que coincidan con este filtro.</div> : null}
    </section>
  )
}
