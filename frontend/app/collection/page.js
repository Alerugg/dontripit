import LibraryPage from '../../components/library/LibraryPage'
import '../../components/library/LibraryPortfolioV2.css'

export const metadata = {
  title: 'Mi colección · Don’tRipIt',
  robots: { index: false, follow: false },
}

export default function CollectionPage() {
  return <LibraryPage kind="collection" />
}
