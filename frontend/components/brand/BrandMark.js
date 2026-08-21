export default function BrandMark({ compact = false, className = '' }) {
  return (
    <span className={`dri-brand dri-brand-official ${compact ? 'dri-brand-compact' : ''} ${className}`.trim()}>
      <img
        src={compact ? '/branding/dontripit-mark.png' : '/branding/dontripit-wordmark.png'}
        alt="Don’tRipIt"
        className={compact ? 'dri-brand-official-mark' : 'dri-brand-official-wordmark'}
        loading="eager"
        decoding="async"
      />
    </span>
  )
}
