import Link from 'next/link'
import BrandMark from '../brand/BrandMark'
import './SiteFooterRedesign.css'

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
          <p>Tu colección TCG, sin caos. Busca, organiza y sigue lo que viene desde una sola cuenta.</p>
          <a href="mailto:info@dontripit.com">info@dontripit.com</a>
        </div>

        <div className="dri-footer-column">
          <strong>Juegos</strong>
          {games.map(([label, href]) => <Link key={href} href={href}>{label}</Link>)}
        </div>

        <div className="dri-footer-column">
          <strong>Don’tRipIt</strong>
          <Link href="/#features">Cómo funciona</Link>
          <Link href="/#news">Noticias</Link>
          <a href="https://shop.dontripit.com">Tienda</a>
          <Link href="/register">Crear cuenta</Link>
        </div>

        <div className="dri-footer-column">
          <strong>Legal</strong>
          <Link href="/privacy">Privacidad</Link>
          <Link href="/terms">Términos del MVP</Link>
          <span>Cookies · pendiente de configuración final</span>
        </div>
      </div>

      <div className="app-shell dri-footer-bottom">
        <span>© 2026 Don’tRipIt</span>
        <span>Las marcas y juegos pertenecen a sus respectivos propietarios.</span>
      </div>
    </footer>
  )
}
