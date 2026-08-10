import Link from 'next/link'
import TopNav from '../../components/layout/TopNav'
import SiteFooter from '../../components/layout/SiteFooter'

export const metadata = {
  title: 'Política de cookies',
  description: 'Información sobre las cookies y tecnologías de almacenamiento utilizadas por Don’tRipIt.',
  alternates: { canonical: '/cookies' },
}

export default function CookiesPage() {
  return (
    <main>
      <TopNav />
      <section className="page-shell" style={{ maxWidth: 860, paddingTop: 56, paddingBottom: 80 }}>
        <p className="dri-kicker">Cookies</p>
        <h1>Usamos solo lo necesario para que tu sesión funcione.</h1>
        <div className="panel" style={{ marginTop: 24, padding: 28 }}>
          <p><strong>Última actualización:</strong> 10 de agosto de 2026.</p>
          <p>Don’tRipIt utiliza actualmente una cookie propia estrictamente necesaria para mantener la sesión de una cuenta autenticada. No utilizamos cookies publicitarias ni cookies de analítica de comportamiento en la aplicación pública actual.</p>

          <h2>Cookie técnica de sesión</h2>
          <p><strong>Nombre:</strong> <code>dri_session</code>.</p>
          <p><strong>Finalidad:</strong> identificar de forma segura una sesión iniciada para permitir el acceso a la colección, wishlist y demás funciones privadas de la cuenta.</p>
          <p><strong>Características:</strong> se configura como HttpOnly, usa Secure en producción, SameSite=Lax y está limitada a este sitio. Su duración está ligada a la vigencia de la sesión y se elimina al cerrar sesión.</p>

          <h2>¿Por qué no aparece un banner de “Aceptar cookies”?</h2>
          <p>La aplicación no instala actualmente cookies opcionales que requieran consentimiento. Mostrar un banner de aceptación sin una finalidad opcional real sería confuso. Si incorporamos analítica, publicidad u otra tecnología no esencial, se bloqueará por defecto hasta obtener una elección válida y se ofrecerán opciones equivalentes para aceptar o rechazar.</p>

          <h2>Servicios y enlaces de terceros</h2>
          <p>Al seguir un enlace externo —por ejemplo a Cardmarket o a una web oficial de un juego— abandonas Don’tRipIt y pasan a aplicarse las políticas del sitio de destino. Don’tRipIt no controla las cookies que esos terceros instalen después de que accedas a sus dominios.</p>

          <h2>Contacto</h2>
          <p>Para consultas sobre privacidad o cookies puedes escribir a <a href="mailto:info@dontripit.com">info@dontripit.com</a>.</p>

          <p style={{ marginTop: 24 }}>
            <Link href="/privacy">Privacidad</Link> · <Link href="/terms">Términos de uso</Link>
          </p>
        </div>
      </section>
      <SiteFooter />
    </main>
  )
}
