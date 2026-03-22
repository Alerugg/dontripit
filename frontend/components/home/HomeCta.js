import Link from 'next/link'
import { homeFinalCtaLinks } from './homeData'

export default function HomeCta() {
  return (
    <section className="home-section home-cta">
      <div className="home-cta-panel home-panel">
        <div className="home-glow-orb home-glow-gold cta-gold" />
        <div className="home-glow-orb home-glow-blue cta-blue" />

        <div className="home-cta-copy">
          <p className="home-kicker">Closing note</p>
          <h2>Construido para explorar. Preparado para escalar.</h2>
          <p>
            Empieza por un TCG y termina con una base lista para catálogo, colección, wishlist, pricing y futuro
            marketplace sin romper el flujo actual.
          </p>
          <small>Diseñado para discovery serio, claridad de datos y expansión modular del producto.</small>
        </div>

        <div className="home-cta-actions">
          {homeFinalCtaLinks.map((item, index) => (
            <Link key={item.href} href={item.href} className={index === 0 ? 'primary-btn' : 'secondary-btn'}>
              {item.label}
            </Link>
          ))}
        </div>
      </div>
    </section>
  )
}
