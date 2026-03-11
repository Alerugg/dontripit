import './globals.css'
import { Inter } from 'next/font/google'

const inter = Inter({ subsets: ['latin'] })

export const metadata = {
  title: 'API-PROJECT · TCG Catalog',
  description: 'Catálogo visual de cartas TCG conectado a la API real de API-PROJECT',
}

export default function RootLayout({ children }) {
  return (
    <html lang="es">
      <body className={inter.className}>{children}</body>
    </html>
  )
}
