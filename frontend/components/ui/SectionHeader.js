import './SectionHeader.css'

export default function SectionHeader({
  eyebrow,
  title,
  description,
  actions = null,
  compact = false,
  align = 'default',
}) {
  return (
    <div className={`section-header-v2 ${compact ? 'is-compact' : ''} ${align === 'between' ? 'is-between' : ''}`}>
      <div className="section-header-v2-copy">
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        {title ? <h2>{title}</h2> : null}
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="section-header-v2-actions">{actions}</div> : null}
    </div>
  )
}