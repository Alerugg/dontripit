import CatalogCard from './CatalogCard'

function buildDebugKeys(items) {
  const debugKeys = new Set()

  for (const game of ['onepiece', 'pokemon']) {
    const probe = items.find((item) => item?.game === game && item?.primary_image_url)
    if (probe) debugKeys.add(`${probe.type || 'item'}-${probe.id}`)
  }

  return debugKeys
}

export default function ResultsGrid({ items, view, queryState }) {
  if (!items.length) return null

  const debugKeys = process.env.NODE_ENV !== 'production' ? buildDebugKeys(items) : new Set()

  return (
    <section className={view === 'list' ? 'results-list' : 'results-grid'}>
      {items.map((item) => {
        const itemKey = `${item.type || 'item'}-${item.id}`
        return (
          <CatalogCard
            key={itemKey}
            item={item}
            view={view}
            queryState={queryState}
            debugImage={debugKeys.has(itemKey)}
          />
        )
      })}
    </section>
  )
}
