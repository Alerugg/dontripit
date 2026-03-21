import Link from 'next/link'
import { homeFinalCtaLinks } from './homeData'

export default function HomeFinalCta() {
  return (
    <section className="home-final-cta panel">
      <div className="home-final-copy">
        <p className="kicker">Siguiente paso</p>
        <h2>Usa esta home como base para catálogo, pricing y marketplace sin volver a empezar desde cero.</h2>
        <p>
          La composición queda lista para crecer con nuevas categorías, planes comerciales, inventory tools o seller
          surfaces manteniendo la misma identidad visual premium.
        </p>
      </div>

      <div className="home-final-actions">
        {homeFinalCtaLinks.map((item, index) => (
          <Link
            key={item.href}
            href={item.href}
            className={index === 0 ? 'primary-btn' : 'secondary-btn'}
          >
            {item.label}
          </Link>
        ))}
      </div>
    </section>
  )
}
