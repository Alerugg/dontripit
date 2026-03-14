'use client'

export default function TypeBadge({ type }) {
  return <span className={`type-badge type-${type}`}>{type}</span>
}
