'use client'

import Link from 'next/link'
import { useState } from 'react'
import BrandMark from '../brand/BrandMark'

export default function AuthShell({ mode = 'register' }) {
  const register = mode === 'register'
  const [message, setMessage] = useState('')

  function onSubmit(event) {
    event.preventDefault()
    setMessage('Vista previa del flujo. La conexión real se activará con Neon Auth antes de pasar este rediseño a producción.')
  }

  return (
    <main className="dri-auth-page">
      <section className="dri-auth-brand-panel">
        <Link href="/" className="dri-auth-logo"><BrandMark /></Link>
        <div className="dri-auth-brand-copy">
          <span className="dri-kicker">Una cuenta. Todos tus TCG.</span>
          <h1>{register ? 'Empieza tu colección en serio.' : 'Bienvenido de vuelta.'}</h1>
          <p>
            Busca cartas, guarda versiones exactas, crea wishlists y sigue próximos lanzamientos desde un único sitio.
          </p>
          <ul>
            <li><span>✓</span> Acceso completo gratuito al inicio</li>
            <li><span>✓</span> Portfolio y wishlist sincronizados</li>
            <li><span>✓</span> Noticias y lanzamientos por juego</li>
          </ul>
        </div>
        <small>Don’tRipIt · Hecho para coleccionistas</small>
      </section>

      <section className="dri-auth-form-panel">
        <div className="dri-auth-form-wrap">
          <div className="dri-auth-mobile-brand"><BrandMark compact /></div>
          <span className="dri-kicker">{register ? 'Crear cuenta' : 'Iniciar sesión'}</span>
          <h2>{register ? 'Tu colección empieza aquí.' : 'Continúa donde lo dejaste.'}</h2>
          <p className="dri-auth-intro">
            {register ? 'Solo necesitamos lo básico. Podrás completar tu perfil después.' : 'Introduce tu correo y contraseña para entrar.'}
          </p>

          <form className="dri-auth-form" onSubmit={onSubmit}>
            {register ? (
              <label>
                <span>Nombre</span>
                <input type="text" name="name" autoComplete="name" placeholder="Tu nombre" required />
              </label>
            ) : null}
            <label>
              <span>Correo electrónico</span>
              <input type="email" name="email" autoComplete="email" placeholder="tu@email.com" required />
            </label>
            <label>
              <span>Contraseña</span>
              <input type="password" name="password" autoComplete={register ? 'new-password' : 'current-password'} placeholder="8 caracteres o más" minLength={8} required />
            </label>

            {register ? (
              <div className="dri-auth-consents">
                <label className="dri-checkbox-row">
                  <input type="checkbox" required />
                  <span>Acepto los términos y la política de privacidad.</span>
                </label>
                <label className="dri-checkbox-row">
                  <input type="checkbox" name="marketing_consent" />
                  <span>Quiero recibir novedades, lanzamientos y contenido de Don’tRipIt por email. <em>Opcional.</em></span>
                </label>
              </div>
            ) : (
              <div className="dri-auth-helper-row">
                <label className="dri-checkbox-row"><input type="checkbox" /> <span>Recordarme</span></label>
                <button type="button" className="dri-link-button">¿Olvidaste tu contraseña?</button>
              </div>
            )}

            <button type="submit" className="dri-btn dri-btn-primary dri-btn-lg dri-auth-submit">
              {register ? 'Crear mi cuenta' : 'Entrar'}
            </button>
          </form>

          {message ? <p className="dri-auth-preview-note">{message}</p> : null}

          <p className="dri-auth-switch">
            {register ? '¿Ya tienes una cuenta?' : '¿Aún no tienes cuenta?'}{' '}
            <Link href={register ? '/login' : '/register'}>{register ? 'Entrar' : 'Crear cuenta gratis'}</Link>
          </p>
        </div>
      </section>
    </main>
  )
}
