import Link from 'next/link'
import TopNav from '../../components/layout/TopNav'
import SiteFooter from '../../components/layout/SiteFooter'

export const metadata = {
  title: 'Privacidad',
  description: 'Información sobre cómo Don’tRipIt trata los datos de cuenta, colección, wishlist y seguridad.',
  alternates: { canonical: '/privacy' },
}

export default function PrivacyPage() {
  return (
    <main>
      <TopNav />
      <section className="page-shell" style={{ maxWidth: 860, paddingTop: 56, paddingBottom: 80 }}>
        <p className="dri-kicker">Privacidad</p>
        <h1>Tu cuenta es para gestionar tu colección.</h1>
        <div className="panel" style={{ marginTop: 24, padding: 28 }}>
          <p><strong>Última actualización:</strong> 14 de agosto de 2026.</p>
          <p>Esta información describe el tratamiento de datos realizado a través del servicio Don’tRipIt en dontripit.com. Para cualquier consulta de privacidad o para ejercer derechos puedes contactar en <a href="mailto:info@dontripit.com">info@dontripit.com</a>.</p>

          <h2>Datos que tratamos</h2>
          <p>Cuando creas una cuenta tratamos el nombre y correo que facilitas, una representación protegida de la contraseña, tokens y datos técnicos de sesión, y la información que guardas voluntariamente en colección o wishlist. También pueden generarse registros técnicos mínimos necesarios para seguridad, diagnóstico y prevención de abuso.</p>

          <h2>Para qué los usamos y base del tratamiento</h2>
          <p>Usamos los datos de cuenta para registrarte, autenticarte, recuperar el acceso, mantener tu colección y wishlist y prestar las funciones que solicitas. El tratamiento necesario para estas funciones se realiza para prestar el servicio solicitado. Las medidas de seguridad y prevención de abuso se aplican para proteger el servicio y las cuentas. Cualquier comunicación de marketing que requiera consentimiento se mantendrá separada y será opcional.</p>

          <h2>Contraseñas, sesiones y recuperación</h2>
          <p>Las contraseñas no se almacenan en texto plano. La sesión del navegador utiliza una cookie técnica HttpOnly. Los enlaces de recuperación son temporales y de un solo uso; al completar un cambio de contraseña se revocan las sesiones anteriores.</p>

          <h2>Proveedores</h2>
          <p>Para operar el servicio utilizamos proveedores de infraestructura y entrega de correo, actualmente incluyendo Vercel para la aplicación y Resend para los mensajes de recuperación. Solo se comparte la información necesaria para prestar esas funciones. Cuando un proveedor trate datos desde fuera del Espacio Económico Europeo, se utilizarán las garantías aplicables que correspondan al servicio contratado.</p>

          <h2>Conservación y eliminación de cuenta</h2>
          <p>Los datos de cuenta y biblioteca se conservan mientras mantengas la cuenta o sean necesarios para prestar el servicio. Las sesiones y credenciales temporales tienen una vigencia limitada.</p>
          <p>Puedes eliminar definitivamente tu cuenta desde la sección Cuenta de Don’tRipIt. El borrado elimina la cuenta y sus datos asociados, incluyendo sesiones, tokens de recuperación, colección y wishlist. Si no puedes iniciar sesión, también puedes iniciar una solicitud externa desde nuestra página pública de <Link href="/delete-account">eliminación de cuenta</Link>.</p>
          <p>Si una obligación legal exigiera conservar información concreta durante un plazo determinado, se conservaría únicamente esa información durante el plazo aplicable y se limitaría su tratamiento a esa finalidad.</p>

          <h2>Tus derechos</h2>
          <p>Puedes solicitar acceso, rectificación, supresión, oposición, limitación o portabilidad cuando proceda, y retirar un consentimiento sin afectar al tratamiento previo. Puedes escribir a <a href="mailto:info@dontripit.com">info@dontripit.com</a>. Si consideras que el tratamiento no es adecuado, también puedes presentar una reclamación ante la autoridad de protección de datos competente.</p>

          <h2>Datos que no vendemos</h2>
          <p>Don’tRipIt no vende los datos personales de tu cuenta a anunciantes. Los enlaces a mercados o sitios oficiales son referencias externas y no convierten los datos de tu colección en datos públicos.</p>

          <h2>Cambios</h2>
          <p>Si una nueva función cambia de forma relevante las finalidades, categorías de datos o destinatarios, actualizaremos esta información antes de aplicar ese cambio cuando sea necesario.</p>

          <p style={{ marginTop: 24 }}>
            <Link href="/cookies">Cookies</Link> · <Link href="/terms">Términos de uso</Link> · <Link href="/delete-account">Eliminar cuenta</Link>
          </p>
        </div>
      </section>
      <SiteFooter />
    </main>
  )
}
