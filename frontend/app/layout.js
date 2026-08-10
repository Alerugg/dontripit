import './globals.css'
import './product-ui.css'
import './auth-ui.css'
import './canva-workspace.css'
import './accessibility.css'
import { Manrope, Space_Grotesk } from 'next/font/google'

const manrope = Manrope({
  subsets: ['latin'],
  variable: '--font-body',
  display: 'swap',
})

const spaceGrotesk = Space_Grotesk({
  subsets: ['latin'],
  variable: '--font-display',
  display: 'swap',
})

const siteName = process.env.NEXT_PUBLIC_SITE_NAME || 'Don’tRipIt'
const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://dontripit.com'

export const metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: `${siteName} · El catálogo TCG para coleccionistas`,
    template: `%s · ${siteName}`,
  },
  description: 'Encuentra la carta, elige la edición física exacta y controla tu colección TCG con fuentes verificables.',
  applicationName: siteName,
  category: 'collectibles',
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      'max-image-preview': 'large',
      'max-snippet': -1,
    },
  },
  openGraph: {
    type: 'website',
    locale: 'es_ES',
    siteName,
    title: `${siteName} · El catálogo TCG para coleccionistas`,
    description: 'Busca cartas, identifica la impresión física exacta y organiza tu colección con referencias verificables.',
    url: siteUrl,
  },
  twitter: {
    card: 'summary',
    title: `${siteName} · El catálogo TCG para coleccionistas`,
    description: 'Busca cartas, identifica la impresión física exacta y organiza tu colección con referencias verificables.',
  },
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
