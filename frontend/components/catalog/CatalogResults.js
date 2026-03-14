import CatalogCard from './CatalogCard'

export default function CatalogResults({ items, view }) {
  if (!items.length) return null

  return (
    <section className={view === 'list' ? 'results-list' : 'results-grid'}>
      {items.map((item) => (
        <CatalogCard key={`${item.type || 'item'}-${item.id}`} item={item} view={view} />
      ))}
    </section>
  )
}
