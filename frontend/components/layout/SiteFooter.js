'use client'

import { useState } from 'react'
import Link from 'next/link'

const CONTACT_EMAIL = 'info@dontripit.com'

const games = [
  { href: '/pokemon', label: 'Pokémon' },
  { href: '/magic', label: 'Magic' },
  { href: '/onepiece', label: 'One Piece' },
  { href: '/yugioh', label: 'Yu-Gi-Oh!' },
  { href: '/riftbound', label: 'Riftbound' },
]

const productLinks = [
  { href: '/docs', label: 'API Docs' },
  { href: '/pokemon', label: 'Explorers' },
  { href: '#', label: 'Marketplace próximamente' },
]

export default function SiteFooter() {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [message, setMessage] = useState('')

  function handleSubmit(event) {
    event.preventDefault()

    const subject = encodeURIComponent(`Contacto DonTripIt · ${name || 'Nuevo mensaje'}`)
    const body = encodeURIComponent(
      `Nombre: ${name}\nEmail: ${email}\n\nMensaje:\n${message}`
    )

    window.location.href = `mailto:${CONTACT_EMAIL}?subject=${subject}&body=${body}`
  }

  return (
    <footer className="site-footer-v3">
      <div className="page-shell footer-shell-v3">
        <div className="footer-panel panel">
          <div className="footer-grid-v3">
            <section className="footer-block footer-brand-block">
              <p className="footer-eyebrow">DonTripIt</p>
              <h3>Catálogo TCG con explorers dedicados y base lista para API y marketplace.</h3>
              <p>
                Una experiencia pensada para navegar cartas, sets y variantes con una estructura clara
                por juego y una dirección seria de producto.
              </p>
              <a href={`mailto:${CONTACT_EMAIL}`} className="footer-email-link">
                {CONTACT_EMAIL}
              </a>
            </section>

            <section className="footer-block footer-links-block">
              <div className="footer-links-group">
                <p className="footer-eyebrow">Juegos</p>
                <ul className="footer-link-list">
                  {games.map((item) => (
                    <li key={item.href}>
                      <Link href={item.href}>{item.label}</Link>
                    </li>
                  ))}
                </ul>
              </div>

              <div className="footer-links-group">
                <p className="footer-eyebrow">Producto</p>
                <ul className="footer-link-list">
                  {productLinks.map((item) => (
                    <li key={item.label}>
                      {item.href === '#' ? (
                        <span className="footer-link-muted">{item.label}</span>
                      ) : (
                        <Link href={item.href}>{item.label}</Link>
                      )}
                    </li>
                  ))}
                  <li>
                    <a href={`mailto:${CONTACT_EMAIL}`}>Contacto</a>
                  </li>
                </ul>
              </div>
            </section>

            <section className="footer-block footer-contact-block">
              <div className="footer-contact-head">
                <p className="footer-eyebrow">Contáctanos</p>
                <h3>Cuéntanos qué necesitas</h3>
                <p>
                  Para partnerships, acceso API, feedback del catálogo o futuras integraciones.
                </p>
              </div>

              <form className="footer-contact-form" onSubmit={handleSubmit}>
                <input
                  type="text"
                  className="input"
                  placeholder="Tu nombre"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  required
                />

                <input
                  type="email"
                  className="input"
                  placeholder="Tu email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  required
                />

                <textarea
                  className="input footer-textarea"
                  placeholder="Tu mensaje"
                  value={message}
                  onChange={(event) => setMessage(event.target.value)}
                  rows={5}
                  required
                />

                <button type="submit" className="primary-btn footer-submit-btn">
                  Enviar mensaje
                </button>
              </form>
            </section>
          </div>

          <div className="footer-bottom-v3">
            <p>© 2026 DonTripIt. Todos los derechos reservados.</p>
            <div className="footer-bottom-links">
              <span>TCG catalog platform</span>
              <span>API-first foundation</span>
            </div>
          </div>
        </div>
      </div>
    </footer>
  )
}