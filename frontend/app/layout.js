import './globals.css'
import './redesign.css'
import './dark-collector.css'
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

export const metadata = {
  title: `${siteName} · Tu colección TCG, sin caos`,
  description: 'Busca cartas, guarda tu colección, crea wishlists y sigue noticias y próximos lanzamientos de tus TCG favoritos.',
}

export default function RootLayout({ children }) {
  return (
    <html lang="es">
      <body className={`${manrope.variable} ${spaceGrotesk.variable}`}>{children}</body>
    </html>
  )
}
