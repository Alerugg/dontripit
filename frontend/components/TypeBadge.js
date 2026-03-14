'use client'

export default function TypeBadge({ type }) {
  return <span className={`type-badge type-${type}`}>{(type || 'item').toUpperCase()}</span>
}
