import Link from 'next/link'
import TopNav from '../../components/layout/TopNav'

export const metadata = { title: 'Términos · Don’tRipIt' }

export default function TermsPage() {
  return (
    <main>
      <TopNav />
      <section className="page-shell" style={{ maxWidth: 860, paddingTop: 56, paddingBottom: 80 }}>
        <p className="dri-kicker">Términos · MVP</p>
        <h1>Un catálogo para coleccionar con claridad.</h1>
        <div className="panel" style={{ marginTop: 24, padding: 28 }}>
          <h2>Servicio</h2>
          <p>Don’tRipIt está en fase MVP. Permite buscar cartas, consultar ediciones físicas y guardar una colección y wishlist personales. Algunas funciones, fuentes y juegos seguirán incorporándose progresivamente.</p>
          <h2>Cuenta</h2>
          <p>Debes proporcionar información veraz para crear tu cuenta y mantener tu contraseña protegida. No debes intentar acceder a cuentas ajenas, automatizar abusivamente el servicio ni interferir con su funcionamiento.</p>
          <h2>Precios</h2>
          <p>Los precios, cuando existan, son datos informativos procedentes de una fuente y fecha registradas. No constituyen una tasación, garantía de venta ni recomendación financiera. Si no existe una observación suficientemente identificada, Don’tRipIt no mostrará una estimación inventada.</p>
          <h2>Contenido de terceros</h2>
          <p>Los nombres, cartas, ilustraciones, marcas y demás materiales de cada TCG pertenecen a sus respectivos titulares. Don’tRipIt es una herramienta de catálogo y colección y no afirma titularidad sobre esas propiedades.</p>
          <h2>Disponibilidad del MVP</h2>
          <p>Durante esta etapa pueden producirse cambios de interfaz, mantenimiento o correcciones de datos. Las reconstrucciones de catálogo se realizan con controles de integridad para evitar mezclar cartas o ediciones físicas.</p>
          <h2>Información legal pendiente</h2>
          <p>Antes del lanzamiento comercial definitivo se incorporarán los datos completos del titular/responsable del servicio y el canal legal de contacto.</p>
          <p style={{ marginTop: 24 }}><Link href="/privacy">Ver información de privacidad</Link></p>
        </div>
      </section>
    </main>
  )
}
