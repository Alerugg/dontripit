import Link from 'next/link'
import TopNav from '../components/layout/TopNav'
import SiteFooter from '../components/layout/SiteFooter'
import styles from './not-found.module.css'

export default function NotFound() {
  return (
    <main className={styles.page}>
      <TopNav />
      <section className={styles.hero} aria-labelledby="not-found-title">
        <div className={styles.grid} aria-hidden="true" />
        <div className={styles.content}>
          <span className={styles.code}>404 · Fuera de catálogo</span>
          <h1 id="not-found-title">Esta página no existe.</h1>
          <p>La ruta puede haber cambiado o la dirección no corresponde a una carta, impresión o sección disponible.</p>
          <div className={styles.actions}>
            <Link href="/explorer" className={styles.primary}>Explorar catálogo</Link>
            <Link href="/" className={styles.secondary}>Volver al inicio</Link>
          </div>
        </div>
        <div className={styles.mark} aria-hidden="true">
          <span>4</span><i>0</i><span>4</span>
        </div>
      </section>
      <SiteFooter />
    </main>
  )
}
