import { homeWhyItems } from './homeData'

export default function HomeWhySection() {
  return (
    <section className="home-why-grid">
      <div className="section-copy home-why-intro">
        <p className="kicker">Por qué Don’tRipIt</p>
        <h2>Una plataforma que se siente premium hoy y coherente con el producto que quieres construir mañana.</h2>
        <p>
          La nueva home se apoya en capas visuales, bloques reutilizables y una narrativa de catálogo serio para
          dejar atrás la sensación de landing estática y centralizada.
        </p>
      </div>

      <div className="home-why-cards">
        {homeWhyItems.map((item) => (
          <article key={item.title} className="home-why-card panel-soft">
            <h3>{item.title}</h3>
            <p>{item.body}</p>
          </article>
        ))}
      </div>
    </section>
  )
}
