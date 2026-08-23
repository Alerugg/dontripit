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
          <div className="dri-footer-logo-safe" aria-label="Don’tRipIt">
            <BrandMark variant="nav" />
          </div>
          <p>Busca la carta, identifica la versión física exacta y organiza colección y wishlist desde una sola cuenta.</p>
          <a href="mailto:info@dontripit.com">info@dontripit.com</a>
        </div>

        <div className="dri-footer-column">
          <strong>Juegos</strong>
          {games.map(([label, href]) => <Link key={href} href={href} prefetch={false}>{label}</Link>)}
        </div>

        <div className="dri-footer-column">
          <strong>Don’tRipIt</strong>
          <Link href="/#how-it-works" prefetch={false}>Cómo funciona</Link>
          <Link href="/#releases" prefetch={false}>Lanzamientos</Link>
          <a href="https://shop.dontripit.com" target="_blank" rel="noopener noreferrer">Tienda ↗</a>
          <Link href="/register" prefetch={false}>Crear cuenta</Link>
        </div>

        <div className="dri-footer-column">
          <strong>Legal</strong>
          <Link href="/privacy" prefetch={false}>Privacidad</Link>
          <Link href="/cookies" prefetch={false}>Cookies</Link>
          <Link href="/terms" prefetch={false}>Términos de uso</Link>
          <a href="mailto:info@dontripit.com">Contacto</a>
        </div>
      </div>

      <div className="app-shell dri-footer-bottom">
        <span>© 2026 Don’tRipIt</span>
        <span>Las marcas y juegos pertenecen a sus respectivos propietarios.</span>
      </div>
    </footer>
  )
}
