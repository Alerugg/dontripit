import CatalogCard from './CatalogCard'
import './ResultsGrid.css'


function buildDebugKeys(items) {
  const debugKeys = new Set()

  for (const game of ['onepiece', 'pokemon']) {
    const probe = items.find((item) => item?.game === game && item?.primary_image_url)
    if (probe) debugKeys.add(`${itemKey(probe)}`)
  }

  return debugKeys
}

function itemKey(item) {
  return `${item.type || 'item'}-${item.id}`
}

export default function ResultsGrid({ items, view, queryState }) {
  if (!items.length) return null

  const debugKeys = process.env.NODE_ENV !== 'production' ? buildDebugKeys(items) : new Set()

  return (
    <section className={view === 'list' ? 'catalog-results-list' : 'catalog-results-grid'}>
      {items.map((item) => {
        const key = itemKey(item)
        return (
          <CatalogCard
            key={key}
            item={item}
            view={view}
            queryState={queryState}
            debugImage={debugKeys.has(key)}
          />
        )
      })}
    </section>
  )
}