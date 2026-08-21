import './globals.css'
import './product-ui.css'
import './auth-ui.css'
import './canva-workspace.css'
import './accessibility.css'
import './lovable-v2.css'
import './lovable-v2-interior.css'
import './lovable-v2-explorer.css'
import './lovable-v2-result-cards.css'
import './lovable-v2-card-detail.css'
import './lovable-v2-shell.css'
import './lovable-v2-home-dashboard.css'
import './lovable-v2-hero.css'
import './lovable-v3-home.css'
import './lovable-v4-home-editorial.css'
import './lovable-v4-home-dark-overrides.css'
import './lovable-v4-home-media.css'
import { Manrope, Space_Grotesk } from 'next/font/google'
import { SITE_NAME, SITE_URL } from '../lib/site'

const manrope = Manrope({ subsets: ['latin'], variable: '--font-body', display: 'swap' })
const spaceGrotesk = Space_Grotesk({ subsets: ['latin'], variable: '--font-display', display: 'swap' })

export const metadata = {
  metadataBase: new URL(SITE_URL),
  title: { default: `${SITE_NAME} · El catálogo TCG para coleccionistas`, template: `%s · ${SITE_NAME}` },
  description: 'Encuentra la carta, elige la edición física exacta y controla tu colección TCG con fuentes verificables.',
  applicationName: SITE_NAME,
  category: 'collectibles',
  robots: { index: true, follow: true, googleBot: { index: true, follow: true, 'max-image-preview': 'large', 'max-snippet': -1 } },
  openGraph: { type: 'website', locale: 'es_ES', siteName: SITE_NAME, title: `${SITE_NAME} · El catálogo TCG para coleccionistas`, description: 'Busca cartas, identifica la impresión física exacta y organiza tu colección con referencias verificables.', url: SITE_URL },
  twitter: { card: 'summary', title: `${SITE_NAME} · El catálogo TCG para coleccionistas`, description: 'Busca cartas, identifica la impresión física exacta y organiza tu colección con referencias verificables.' },
}

export default function RootLayout({ children }) {
  return (
    <html lang="es">
      <body className={`${manrope.variable} ${spaceGrotesk.variable}`}>
        <a className="dri-skip-link" href="#main-content">Saltar al contenido principal</a>
        <div id="main-content" tabIndex={-1}>{children}</div>
      </body>
    </html>
  )
}
