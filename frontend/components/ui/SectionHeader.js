export default function SectionHeader({
  eyebrow,
  title,
  description,
  actions = null,
  compact = false,
  align = 'default',
}) {
  return (
    <div className={`section-heading ${compact ? 'compact' : ''} ${align === 'between' ? 'section-heading-between' : ''}`}>
      <div className="section-heading-copy">
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        {title ? <h2>{title}</h2> : null}
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="section-heading-actions">{actions}</div> : null}
    </div>
  )
}
