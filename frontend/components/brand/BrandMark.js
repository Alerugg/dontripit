export default function BrandMark({ compact = false }) {
  return (
    <span className={`dri-brand ${compact ? 'dri-brand-compact' : ''}`}>
      <span className="dri-brand-symbol" aria-hidden="true">
        <svg viewBox="0 0 40 40" role="img">
          <rect x="7" y="5" width="26" height="30" rx="7" fill="currentColor" opacity="0.12" />
          <path d="M13 9.5h14a2 2 0 0 1 2 2v17a2 2 0 0 1-2 2H13a2 2 0 0 1-2-2v-17a2 2 0 0 1 2-2Z" fill="none" stroke="currentColor" strokeWidth="2.2" />
          <path d="M15 20h10" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeDasharray="2.5 3.4" />
          <path d="m24.5 17.2 3 2.8-3 2.8" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
      <span className="dri-brand-wordmark">
        <span>DON’T</span><strong>RIPIT</strong>
      </span>
    </span>
  )
}
