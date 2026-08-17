import Link from 'next/link'
import TopNav from '../../components/layout/TopNav'
import SiteFooter from '../../components/layout/SiteFooter'

export const metadata = {
  title: 'Términos de uso',
  description: 'Condiciones de uso del catálogo y las herramientas de colección de Don’tRipIt.',
  alternates: { canonical: '/terms' },
}

export default function TermsPage() {
  return (
    <main>
      <TopNav />
      <section className="page-shell" style={{ maxWidth: 860, paddingTop: 56, paddingBottom: 80 }}>
        <p className="dri-kicker">Términos de uso</p>
        <h1>Un catálogo para identificar y organizar coleccionables.</h1>
        <div className="panel" style={{ marginTop: 24, padding: 28 }}>
          <p><strong>Última actualización:</strong> 10 de agosto de 2026.</p>
          <p>Estos términos regulan el uso de Don’tRipIt en dontripit.com. Para consultas relacionadas con el servicio puedes escribir a <a href="mailto:info@dontripit.com">info@dontripit.com</a>.</p>

          <h2>Qué ofrece Don’tRipIt</h2>
          <p>Don’tRipIt permite buscar cartas y productos TCG, distinguir impresiones físicas, consultar referencias de mercado y organizar una colección y wishlist personales. Algunas secciones pueden identificarse expresamente como próximas, experimentales o en evolución.</p>

          <h2>Cuenta y seguridad</h2>
          <p>Debes facilitar información veraz al crear una cuenta y proteger tus credenciales. No puedes intentar acceder a cuentas ajenas, eludir controles de seguridad, saturar deliberadamente el servicio, extraer datos de forma abusiva ni utilizar Don’tRipIt para una actividad ilícita.</p>

          <h2>Precios y referencias de mercado</h2>
          <p>Los precios mostrados son referencias informativas asociadas a una fuente, producto y fecha concretos. No constituyen una tasación profesional, una oferta de compra o venta, una garantía de liquidez ni una recomendación de inversión. Cuando no existe una referencia suficientemente identificada, Don’tRipIt evita inventar un precio.</p>

          <h2>Enlaces de terceros</h2>
          <p>Los botones que llevan a Cardmarket u otros sitios externos abren servicios gestionados por terceros. Sus precios, disponibilidad, condiciones, políticas y operaciones son responsabilidad de esos terceros. Comprueba siempre la versión, idioma, condición y vendedor antes de comprar.</p>

          <h2>Contenido y marcas de terceros</h2>
          <p>Los nombres, ilustraciones, logotipos, cartas, juegos y demás materiales identificativos de cada TCG pertenecen a sus respectivos titulares. Don’tRipIt los referencia con fines de identificación y catálogo y no afirma ser propietario ni representante oficial de esas marcas salvo que se indique expresamente.</p>

          <h2>Exactitud y disponibilidad</h2>
          <p>Trabajamos para mantener separadas las distintas impresiones físicas y conservar la trazabilidad de las fuentes, pero un catálogo de terceros puede cambiar, corregirse o dejar de estar disponible. Podemos corregir errores, retirar enlaces inseguros, actualizar fuentes o realizar mantenimiento cuando sea necesario.</p>

          <h2>Tu contenido de cuenta</h2>
          <p>La información que añades a colección o wishlist se usa para prestarte esas funciones y no convierte automáticamente tu biblioteca en pública. Eres responsable de que cualquier información que introduzcas sea lícita.</p>

          <h2>Cambios del servicio</h2>
          <p>Podemos modificar funciones, límites técnicos o estos términos cuando el producto evolucione. Si un cambio afecta de forma relevante a derechos o al tratamiento de datos, se comunicará o solicitará una nueva aceptación cuando corresponda.</p>

          <h2>Derechos obligatorios</h2>
          <p>Nada de estos términos pretende excluir derechos que la normativa aplicable reconozca con carácter obligatorio a usuarios o consumidores.</p>

          <p style={{ marginTop: 24 }}>
            <Link href="/privacy">Privacidad</Link> · <Link href="/cookies">Cookies</Link>
          </p>
        </div>
      </section>
      <SiteFooter />
    </main>
  )
}
