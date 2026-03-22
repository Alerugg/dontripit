import { homeFaqItems } from './homeData'

export default function HomeFaq() {
  return (
    <section className="home-section home-faq">
      <div className="home-section-heading">
        <p className="home-kicker">FAQ</p>
        <h2>Dudas clave sobre el alcance actual del catálogo y su evolución como plataforma.</h2>
      </div>

      <div className="home-faq-list">
        {homeFaqItems.map((item) => (
          <details key={item.question} className="home-faq-item home-panel-soft">
            <summary>
              <span>{item.question}</span>
              <span className="home-faq-icon" aria-hidden="true" />
            </summary>
            <p>{item.answer}</p>
          </details>
        ))}
      </div>
    </section>
  )
}
