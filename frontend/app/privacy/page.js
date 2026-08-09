import Link from 'next/link'
import TopNav from '../../components/layout/TopNav'

export const metadata = { title: 'Privacidad · Don’tRipIt' }

export default function PrivacyPage() {
  return (
    <main>
      <TopNav />
      <section className="page-shell" style={{ maxWidth: 860, paddingTop: 56, paddingBottom: 80 }}>
        <p className="dri-kicker">Privacidad · MVP</p>
        <h1>Tu cuenta es para tu colección, no para vender tus datos.</h1>
        <div className="panel" style={{ marginTop: 24, padding: 28 }}>
          <p>Don’tRipIt usa el nombre y el correo que facilitas para crear y mantener tu cuenta, guardar tu colección y wishlist, y proteger el acceso a esos datos.</p>
          <h2>Qué guardamos</h2>
          <p>Nombre, correo, credenciales protegidas mediante hash, sesiones de acceso y las ediciones que añades a colección o wishlist. Si en el futuro añades datos como precio de compra o condición, se usarán únicamente para las funciones de tu cuenta salvo que se informe expresamente de otra finalidad.</p>
          <h2>Marketing</h2>
          <p>El consentimiento de marketing es independiente y opcional. No se venderán los datos de cuenta a anunciantes. Antes de iniciar campañas comerciales se completará la información legal del responsable y el canal específico para retirar ese consentimiento.</p>
          <h2>Seguridad</h2>
          <p>Las contraseñas no se almacenan en texto plano. Las sesiones del navegador se mantienen mediante cookies HttpOnly y el servidor conserva únicamente el hash del token de sesión.</p>
          <h2>Información legal pendiente de completar</h2>
          <p>La identidad fiscal y los datos de contacto del responsable del tratamiento deben incorporarse aquí antes de utilizar este MVP para campañas de marketing o un lanzamiento comercial definitivo. Esta versión se publica como MVP técnico y no sustituye esa información obligatoria.</p>
          <p style={{ marginTop: 24 }}><Link href="/terms">Ver términos del MVP</Link></p>
        </div>
      </section>
    </main>
  )
}
