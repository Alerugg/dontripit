import './globals.css'
import './product-ui.css'
import './auth-ui.css'
import './canva-workspace.css'
import './accessibility.css'
import './pwa.css'
import { Manrope, Space_Grotesk } from 'next/font/google'
import PwaBootstrap from '../components/pwa/PwaBootstrap'
import { SITE_NAME, SITE_URL } from '../lib/site'

const manrope = Manrope({ subsets: ['latin'], variable: '--font-body', display: 'swap' })
const spaceGrotesk = Space_Grotesk({ subsets: ['latin'], variable: '--font-display', display: 'swap' })

export const metadata = {
  metadataBase: new URL(SITE_URL),
  title: { default: `${SITE_NAME} · El catálogo TCG para coleccionistas`, template: `%s · ${SITE_NAME}` },
  description: 'Encuentra la carta, elige la edición física exacta y controla tu colección TCG con fuentes verificables.',
  applicationName: SITE_NAME,
  category: 'collectibles',
  manifest: '/manifest.webmanifest',
  icons: {
    icon: [
      { url: '/icons/dontripit-192.png', sizes: '192x192', type: 'image/png' },
      { url: '/icons/dontripit-512.png', sizes: '512x512', type: 'image/png' },
    ],
    apple: [{ url: '/icons/dontripit-192.png', sizes: '192x192', type: 'image/png' }],
  },
  appleWebApp: {
    capable: true,
    title: SITE_NAME,
    statusBarStyle: 'black-translucent',
  },
  robots: { index: true, follow: true, googleBot: { index: true, follow: true, 'max-image-preview': 'large', 'max-snippet': -1 } },
  openGraph: { type: 'website', locale: 'es_ES', siteName: SITE_NAME, title: `${SITE_NAME} · El catálogo TCG para coleccionistas`, description: 'Busca cartas, identifica la impresión física exacta y organiza tu colección con referencias verificables.', url: SITE_URL },
  twitter: { card: 'summary', title: `${SITE_NAME} · El catálogo TCG para coleccionistas`, description: 'Busca cartas, identifica la impresión física exacta y organiza tu colección con referencias verificables.' },
}

export const viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
  themeColor: '#121018',
  colorScheme: 'dark',
}

export default function RootLayout({ children }) {
  return (
    <html lang="es">
      <body className={`${manrope.variable} ${spaceGrotesk.variable}`}>
        <PwaBootstrap />
        <a className="dri-skip-link" href="#main-content">Saltar al contenido principal</a>
        <div id="main-content" tabIndex={-1}>{children}</div>
      </body>
    </html>
  )
}
