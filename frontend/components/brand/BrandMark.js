const BRAND_ASSETS = {
  nav: '/branding/dontripit_logo.png',
  wordmark: '/branding/dontripit-wordmark-ui.webp',
}

export default function BrandMark({ compact = false, variant = 'wordmark', className = '' }) {
  const resolvedVariant = variant === 'nav' || compact ? 'nav' : 'wordmark'
  const src = BRAND_ASSETS[resolvedVariant]

  return (
    <span className={`dri-brand dri-brand-official dri-brand-${resolvedVariant} ${className}`.trim()}>
      <img
        src={src}
        alt="Don’tRipIt"
        className={resolvedVariant === 'nav' ? 'dri-brand-official-mark' : 'dri-brand-official-wordmark'}
        loading="eager"
        decoding="async"
      />
    </span>
  )
}
