'use client'

import { useEffect, useMemo, useState } from 'react'
import { buildCatalogImageDebugInfo, normalizeCatalogImageSrc } from '../../lib/catalog/image'

function initialsFromText(text = '') {
  const chunks = String(text)
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)

  if (chunks.length === 0) return 'TCG'
  return chunks.map((chunk) => chunk[0]?.toUpperCase() || '').join('')
}

export default function FallbackImage({
  src,
  alt,
  className,
  placeholderClassName,
  label,
  initials,
  debug = false,
  debugLabel = '',
}) {
  const [status, setStatus] = useState('idle')
  const [failureSrc, setFailureSrc] = useState('')
  const safeLabel = label || alt || 'Sin imagen'
  const imageSrc = normalizeCatalogImageSrc(src)
  const placeholderInitials = useMemo(() => initials || initialsFromText(safeLabel), [initials, safeLabel])

  useEffect(() => {
    setFailureSrc('')
    setStatus(imageSrc ? 'loading' : 'no-src')
  }, [imageSrc])

  const debugInfo = buildCatalogImageDebugInfo({
    src,
    normalizedSrc: imageSrc,
    status: imageSrc && failureSrc ? 'error' : status,
    reason: imageSrc ? (failureSrc ? 'load-error' : '') : 'missing-src',
  })

  if (!imageSrc || failureSrc) {
    return (
      <div
        className={placeholderClassName || 'image-fallback'}
        role="img"
        aria-label={`Placeholder para ${safeLabel}`}
        data-image-state={debugInfo.status}
        data-image-original-src={debugInfo.originalSrc}
        data-image-normalized-src={debugInfo.normalizedSrc}
        data-image-failure-src={failureSrc}
      >
        <span className="image-fallback-badge">{placeholderInitials}</span>
        <span className="image-fallback-text">{safeLabel}</span>
        {debug ? <small>{`${debugLabel || safeLabel} · ${debugInfo.status} · ${debugInfo.normalizedSrc || debugInfo.originalSrc || 'no-src'}`}</small> : null}
      </div>
    )
  }

  return (
    <img
      key={imageSrc}
      src={imageSrc}
      alt={alt || safeLabel}
      className={className}
      loading="lazy"
      decoding="async"
      data-image-state={debugInfo.status}
      data-image-original-src={debugInfo.originalSrc}
      data-image-normalized-src={debugInfo.normalizedSrc}
      onLoad={() => setStatus('loaded')}
      onError={(event) => {
        setFailureSrc(event.currentTarget.currentSrc || imageSrc)
        setStatus('error')
      }}
    />
  )
}
