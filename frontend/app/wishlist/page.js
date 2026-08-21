import LibraryPage from '../../components/library/LibraryPage'
import '../../components/library/LibraryPortfolioV2.css'

export const metadata = {
  title: 'Mi wishlist · Don’tRipIt',
  robots: { index: false, follow: false },
}

export default function WishlistPage() {
  return <LibraryPage kind="wishlist" />
}
