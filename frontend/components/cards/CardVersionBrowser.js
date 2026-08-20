'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import FallbackImage from '../common/FallbackImage'
import { fetchCardVersions } from '../../lib/catalog/client'
import { getPrintHref } from '../../lib/catalog/routes'
import styles from './CardVersionBrowser.module.css'

const PAGE_SIZE = 12
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
    version?.collector_number ? `#${version.collector_number}` : null,
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
    print?.collector_number ? `#${print.collector_number}` : null,
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
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE)

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
          setError(requestError?.message || 'No pudimos cargar las impresiones de esta carta.')
        }
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [cardId])

  useEffect(() => {
    setVisibleCount(PAGE_SIZE)
  }, [query, language, cardId])

  const versions = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    return (payload?.versions || []).filter((version) => {
      if (language && !(version.languages || []).some((item) => item.code === language)) return false
      if (normalizedQuery && !versionSearchText(version).includes(normalizedQuery)) return false
      return true
    })
  }, [payload, query, language])

  const displayedVersions = versions.slice(0, visibleCount)

  if (loading) return <section className={styles.state}><p>Organizando versiones e impresiones físicas…</p></section>
  if (error) return <section className={`${styles.state} ${styles.stateError}`}><p>{error}</p></section>

  const allLanguages = payload?.languages || []
  const totalPrints = Number(payload?.print_count || 0)
  const totalVersions = Number(payload?.version_count || (payload?.versions || []).length)

  return (
    <section className={styles.browser} aria-labelledby="card-versions-title">
      <div className={styles.heading}>
        <div>
          <p className="eyebrow">Impresiones físicas</p>
          <h2 id="card-versions-title">Elige la edición exacta</h2>
          <p className={styles.headingCopy}>Cada enlace de idioma termina en una Print concreta. El precio y Cardmarket se verifican después sobre esa identidad exacta.</p>
        </div>
        <div className={styles.counts} aria-label="Cobertura física">
          <span><strong>{totalPrints.toLocaleString('es-ES')}</strong> prints</span>
          <span><strong>{totalVersions.toLocaleString('es-ES')}</strong> versiones</span>
          <span><strong>{allLanguages.length}</strong> idiomas</span>
        </div>
      </div>

      <div className={styles.filters}>
        <label className={styles.searchField}>
          <span className={styles.srOnly}>Filtrar versiones de esta carta</span>
          <input
            className={styles.search}
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Set, código, rareza o variante…"
            aria-label="Filtrar versiones de esta carta"
          />
        </label>
        <div className={styles.languageStrip} aria-label={`Idiomas disponibles en ${cardName || 'esta carta'}`}>
          <button type="button" onClick={() => setLanguage('')} className={`${styles.languageButton} ${!language ? styles.languageButtonActive : ''}`}>Todos</button>
          {allLanguages.map((code) => (
            <button key={code} type="button" onClick={() => setLanguage(code)} className={`${styles.languageButton} ${language === code ? styles.languageButtonActive : ''}`}>
              {languageLabel(code)}
            </button>
          ))}
        </div>
      </div>

      <div className={styles.resultSummary} aria-live="polite">
        <span>{versions.length.toLocaleString('es-ES')} versión{versions.length === 1 ? '' : 'es'} visible{versions.length === 1 ? '' : 's'}</span>
        {(query || language) ? <button type="button" onClick={() => { setQuery(''); setLanguage('') }}>Limpiar filtros</button> : null}
      </div>

      <div className={styles.grid}>
        {displayedVersions.map((version) => {
          const exactPrints = version.prints || []
          const representative = version.representative_print || exactPrints[0] || null
          const singleExactPrint = exactPrints.length === 1 ? exactPrints[0] : null
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
                  <Link href={representativeHref} className={styles.mediaLink} aria-label={`Abrir impresión física de ${versionTitle}`}>{image}</Link>
                ) : image}
                <span className={`${styles.identityBadge} ${version.market_status === 'linked' ? styles.identityLinked : styles.identityUnlinked}`}>
                  {version.market_status === 'linked' ? 'Identidad Cardmarket enlazada' : 'Sin enlace Cardmarket exacto'}
                </span>
              </div>

              <div className={styles.cardBody}>
                <div className={styles.titleBlock}>
                  <div className={styles.titleMetaRow}>
                    {setCode ? <span className={styles.setCode}>{setCode}</span> : null}
                    <span className={styles.physicalCount}>{exactPrints.length} print{exactPrints.length === 1 ? '' : 's'}</span>
                  </div>
                  <h3 title={versionTitle}>{versionTitle}</h3>
                  <p className={styles.meta}>{versionMeta(version) || 'Edición física identificada'}</p>
                </div>

                <div className={styles.languageBlock}>
                  <span className={styles.languageLabel}>Selecciona idioma / impresión</span>
                  <div className={styles.languages}>
                    {(version.languages || []).map((item) => {
                      const languagePrints = printsForLanguage(version, item.code)
                      if (!languagePrints.length) return <span key={item.code} className={`${styles.languageChip} ${styles.languageChipMuted}`}>{languageLabel(item.code)}</span>
                      if (languagePrints.length === 1) {
                        const exactPrint = languagePrints[0]
                        return (
                          <Link
                            key={item.code}
                            href={getPrintHref(exactPrint.print_id)}
                            className={styles.languageChip}
                            title={`Abrir Print ${exactPrint.print_id} · ${languageLabel(item.code)}`}
                          >
                            {languageLabel(item.code)} <span aria-hidden="true">→</span>
                          </Link>
                        )
                      }
                      return (
                        <details key={item.code} className={styles.languageDetails}>
                          <summary className={styles.languageChip}>{languageLabel(item.code)} · {languagePrints.length}</summary>
                          <div className={styles.printChoices}>
                            <p>Elige la Print exacta</p>
                            {languagePrints.map((print) => (
                              <Link key={print.print_id} href={getPrintHref(print.print_id)} className={styles.printChoice}>
                                <span>{exactPrintLabel(print) || `Versión ${languageLabel(item.code)}`}</span>
                                <small>Print {print.print_id} →</small>
                              </Link>
                            ))}
                          </div>
                        </details>
                      )
                    })}
                  </div>
                </div>

                <div className={styles.actions}>
                  {singleExactPrint?.print_id ? (
                    <Link href={getPrintHref(singleExactPrint.print_id)} className={styles.primaryAction}>Abrir impresión exacta →</Link>
                  ) : (
                    <span className={styles.selectionHint}>Elige un idioma arriba para abrir la impresión exacta.</span>
                  )}
                  {version.cardmarket?.url ? (
                    <a href={version.cardmarket.url} target="_blank" rel="noopener noreferrer sponsored" className={styles.referenceLink}>Referencia de producto Cardmarket ↗</a>
                  ) : null}
                </div>
              </div>
            </article>
          )
        })}
      </div>

      {visibleCount < versions.length ? (
        <button type="button" className={styles.loadMore} onClick={() => setVisibleCount((current) => current + PAGE_SIZE)}>
          Ver más versiones ({versions.length - visibleCount})
        </button>
      ) : null}

      {!versions.length ? <div className={styles.empty}>No hay versiones que coincidan con este filtro.</div> : null}
    </section>
  )
}
