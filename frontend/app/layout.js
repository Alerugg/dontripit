import './globals.css'
import { Inter } from 'next/font/google'

const inter = Inter({ subsets: ['latin'] })
const siteName = process.env.NEXT_PUBLIC_SITE_NAME || 'Don’tRipIt'

export const metadata = {
  title: `${siteName} · Multi-game Catalog`,
  description: 'Catálogo multi-juego para colección y marketplace de Don’tRipIt.',
}

export default function RootLayout({ children }) {
  return (
    <html lang="es">
      <body className={inter.className}>{children}</body>
    </html>
  )
}
