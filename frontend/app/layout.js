import './globals.css'
import { Inter } from 'next/font/google'

const inter = Inter({ subsets: ['latin'] })
const siteName = process.env.NEXT_PUBLIC_SITE_NAME || 'TCG Nexus'

export const metadata = {
  title: `${siteName} · Multi-game Catalog`,
  description: 'Catálogo público de cartas/prints y consola admin interna para API-PROJECT',
}

export default function RootLayout({ children }) {
  return (
    <html lang="es">
      <body className={inter.className}>{children}</body>
    </html>
  )
}
