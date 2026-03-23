import ResultsGrid from '../catalog/ResultsGrid'
import SectionHeader from '../ui/SectionHeader'

export default function GameResultsGrid({ items, view, queryState }) {
  return (
    <section className="game-section game-results-section">
      <SectionHeader
        compact
        eyebrow="Resultados"
        title="Cartas"
        description={`${items.length} resultado${items.length === 1 ? '' : 's'} en la vista actual.`}
      />
      <ResultsGrid items={items} view={view} queryState={queryState} />
    </section>
  )
}
