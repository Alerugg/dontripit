import Link from 'next/link'
import BrandMark from '../brand/BrandMark'

const games = [
  ['Pokémon', '/games/pokemon'],
  ['Magic', '/games/magic'],
  ['One Piece', '/games/onepiece'],
  ['Yu-Gi-Oh!', '/games/yugioh'],
  ['Riftbound · próximamente', '/games/riftbound'],
]

export default function SiteFooter() {
  return (
    <footer className="dri-footer">
      <div className="app-shell dri-footer-grid">
        <div className="dri-footer-brand">
          <BrandMark />
          <p>Busca la carta, identifica la versión física exacta y organiza colección y wishlist desde una sola cuenta.</p>
          <a href="mailto:info@dontripit.com">info@dontripit.com</a>
        </div>

        <div className="dri-footer-column">
          <strong>Juegos</strong>
          {games.map(([label, href]) => <Link key={href} href={href}>{label}</Link>)}
        </div>

        <div className="dri-footer-column">
          <strong>Don’tRipIt</strong>
          <Link href="/#how-it-works">Cómo funciona</Link>
          <Link href="/#releases">Lanzamientos</Link>
          <a href="https://shop.dontripit.com" target="_blank" rel="noopener noreferrer">Tienda ↗</a>
          <Link href="/register">Crear cuenta</Link>
        </div>

        <div className="dri-footer-column">
          <strong>Legal</strong>
          <Link href="/privacy">Privacidad</Link>
          <Link href="/terms">Términos del MVP</Link>
        </div>
      </div>

      <div className="app-shell dri-footer-bottom">
        <span>© 2026 Don’tRipIt</span>
        <span>Las marcas y juegos pertenecen a sus respectivos propietarios.</span>
      </div>
    </footer>
  )
}
