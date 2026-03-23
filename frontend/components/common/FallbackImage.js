'use client'

import { useEffect, useMemo, useState } from 'react'

function initialsFromText(text = '') {
  const chunks = String(text)
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)

  if (chunks.length === 0) return 'TCG'
  return chunks.map((chunk) => chunk[0]?.toUpperCase() || '').join('')
}

function normalizeSrc(src) {
  if (typeof src !== 'string') return ''

  const trimmed = src.trim()
  if (!trimmed) return ''

  const normalized = trimmed.startsWith('//') ? `https:${trimmed}` : trimmed

  try {
    return encodeURI(normalized)
  } catch {
    return normalized
  }
}

export default function FallbackImage({
  src,
  alt,
  className,
  placeholderClassName,
  label,
  initials,
}) {
  const [hasError, setHasError] = useState(false)
  const safeLabel = label || alt || 'Sin imagen'
  const imageSrc = normalizeSrc(src)
  const placeholderInitials = useMemo(() => initials || initialsFromText(safeLabel), [initials, safeLabel])

  useEffect(() => {
    setHasError(false)
  }, [imageSrc])

  if (!imageSrc || hasError) {
    return (
      <div className={placeholderClassName || 'image-fallback'} role="img" aria-label={`Placeholder para ${safeLabel}`}>
        <span className="image-fallback-badge">{placeholderInitials}</span>
        <span className="image-fallback-text">{safeLabel}</span>
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
      onError={() => setHasError(true)}
    />
  )
}
