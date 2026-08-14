import Link from 'next/link'
import TopNav from '../../components/layout/TopNav'
import SiteFooter from '../../components/layout/SiteFooter'
import { PRIVACY_EMAIL } from '../../lib/site'

export const metadata = {
  title: 'Eliminar cuenta',
  description: 'Cómo eliminar una cuenta de Don’tRipIt y los datos asociados.',
  robots: { index: false, follow: false },
}

const subject = encodeURIComponent('Solicitud de eliminación de cuenta Don’tRipIt')
const body = encodeURIComponent(
  'Hola,\n\nQuiero solicitar la eliminación de mi cuenta de Don’tRipIt y de los datos asociados.\n\nCorreo de la cuenta: [escribe aquí el correo con el que te registraste]\n\nGracias.',
)
const deletionMailto = `mailto:${PRIVACY_EMAIL}?subject=${subject}&body=${body}`

export default function DeleteAccountPage() {
  return (
    <main>
      <TopNav />
      <section className="page-shell" style={{ maxWidth: 860, paddingTop: 56, paddingBottom: 80 }}>
        <p className="dri-kicker">Cuenta y privacidad</p>
        <h1>Eliminar tu cuenta de Don’tRipIt</h1>
        <p style={{ maxWidth: 720, marginTop: 14 }}>
          Puedes iniciar la eliminación desde tu cuenta o solicitarla desde esta página aunque no puedas iniciar sesión.
        </p>

        <div className="panel" style={{ marginTop: 24, padding: 28 }}>
          <h2>Si puedes entrar en tu cuenta</h2>
          <p>
            Entra en <strong>Cuenta</strong>, abre “Privacidad y control de tu cuenta” y pulsa “Eliminar mi cuenta”.
            Para evitar borrados accidentales tendrás que introducir tu contraseña y escribir <strong>ELIMINAR</strong>.
          </p>
          <p>
            <Link href="/dashboard#account-settings" className="dri-btn dri-btn-primary">Ir a mi cuenta</Link>
          </p>

          <h2 style={{ marginTop: 34 }}>Si no puedes iniciar sesión</h2>
          <p>
            Puedes solicitar la eliminación externamente escribiendo a <a href={`mailto:${PRIVACY_EMAIL}`}>{PRIVACY_EMAIL}</a>.
            Para ayudarnos a localizar la cuenta, envía la solicitud desde el correo asociado o indica cuál es el correo de la cuenta.
          </p>
          <p>
            <a href={deletionMailto} className="dri-btn dri-btn-ghost">Solicitar eliminación por email</a>
          </p>
          <p><strong>Nunca envíes tu contraseña, tokens de sesión ni códigos de recuperación por email.</strong></p>

          <h2 style={{ marginTop: 34 }}>Qué se elimina</h2>
          <p>
            Al completar el borrado se elimina la cuenta de Don’tRipIt y los datos asociados a ella: nombre y correo de cuenta,
            credencial protegida, sesiones, tokens de recuperación, colección y wishlist. La eliminación de cuenta es definitiva.
          </p>

          <h2 style={{ marginTop: 34 }}>Qué ocurre después</h2>
          <p>
            Ya no podrás iniciar sesión ni recuperar la colección o wishlist eliminadas. Si existiera alguna obligación legal que
            requiriera conservar información concreta durante un plazo determinado, se aplicaría únicamente a esa información y
            se trataría según la <Link href="/privacy">Política de privacidad</Link>.
          </p>

          <p style={{ marginTop: 28 }}>
            <Link href="/privacy">Privacidad</Link> · <Link href="/terms">Términos de uso</Link>
          </p>
        </div>
      </section>
      <SiteFooter />
    </main>
  )
}
