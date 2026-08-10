import './globals.css'
import './redesign.css'
import './dark-collector.css'
import './ux-v3.css'
import './pricing-v1.css'
import './collector-v4.css'
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
  title: `${siteName} · El catálogo TCG para coleccionistas`,
  description: 'Encuentra la carta, elige la edición física exacta y controla tu colección TCG con fuentes verificables.',
}

export default function RootLayout({ children }) {
  return (
    <html lang="es">
      <body className={`${manrope.variable} ${spaceGrotesk.variable}`}>{children}</body>
    </html>
  )
}
