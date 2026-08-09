'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useState } from 'react'
import BrandMark from '../brand/BrandMark'

function safeNext(value) {
  const target = String(value || '').trim()
  if (!target.startsWith('/') || target.startsWith('//')) return '/dashboard'
  if (target.startsWith('/login') || target.startsWith('/register')) return '/dashboard'
  return target
}

export default function AuthShell({ mode = 'register', next = '/dashboard' }) {
  const register = mode === 'register'
  const router = useRouter()
  const nextPath = safeNext(next)
  const switchHref = `${register ? '/login' : '/register'}${nextPath !== '/dashboard' ? `?next=${encodeURIComponent(nextPath)}` : ''}`
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function onSubmit(event) {
    event.preventDefault()
    setError('')
    setSubmitting(true)
    const form = new FormData(event.currentTarget)
    const payload = register
      ? {
          name: form.get('name'),
          email: form.get('email'),
          password: form.get('password'),
          terms_accepted: form.get('terms_accepted') === 'on',
          marketing_consent: form.get('marketing_consent') === 'on',
        }
      : {
          email: form.get('email'),
          password: form.get('password'),
          remember: form.get('remember') === 'on',
        }

    try {
      const response = await fetch(register ? '/api/auth/register' : '/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.message || 'No pudimos completar el acceso. Inténtalo de nuevo.')
      router.replace(nextPath)
      router.refresh()
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="dri-auth-page">
      <section className="dri-auth-brand-panel">
        <Link href="/" className="dri-auth-logo"><BrandMark /></Link>
        <div className="dri-auth-brand-copy">
          <span className="dri-kicker">Una cuenta. Todos tus TCG.</span>
          <h1>{register ? 'Empieza tu colección en serio.' : 'Bienvenido de vuelta.'}</h1>
          <p>
            Busca cartas, guarda ediciones exactas, crea wishlists y sigue tu colección desde un único sitio.
          </p>
          <ul>
            <li><span>✓</span> Acceso gratuito durante el MVP</li>
            <li><span>✓</span> Colección y wishlist sincronizadas</li>
            <li><span>✓</span> Cada edición física guardada por separado</li>
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
            {register ? 'Solo necesitamos lo básico. Podrás organizar tu colección inmediatamente.' : 'Introduce tu correo y contraseña para entrar.'}
          </p>

          <form className="dri-auth-form" onSubmit={onSubmit}>
            {register ? (
              <label>
                <span>Nombre</span>
                <input type="text" name="name" autoComplete="name" placeholder="Tu nombre" minLength={2} maxLength={120} required />
              </label>
            ) : null}
            <label>
              <span>Correo electrónico</span>
              <input type="email" name="email" autoComplete="email" placeholder="tu@email.com" maxLength={320} required />
            </label>
            <label>
              <span>Contraseña</span>
              <input type="password" name="password" autoComplete={register ? 'new-password' : 'current-password'} placeholder="8 caracteres o más" minLength={8} maxLength={200} required />
            </label>

            {register ? (
              <div className="dri-auth-consents">
                <label className="dri-checkbox-row">
                  <input type="checkbox" name="terms_accepted" required />
                  <span>Acepto los <Link href="/terms">términos</Link> y la <Link href="/privacy">política de privacidad</Link>.</span>
                </label>
                <label className="dri-checkbox-row">
                  <input type="checkbox" name="marketing_consent" />
                  <span>Quiero recibir novedades y lanzamientos de Don’tRipIt por email. <em>Opcional.</em></span>
                </label>
              </div>
            ) : (
              <div className="dri-auth-helper-row">
                <label className="dri-checkbox-row"><input type="checkbox" name="remember" /> <span>Mantener mi sesión</span></label>
              </div>
            )}

            {error ? <p className="dri-auth-preview-note" role="alert">{error}</p> : null}

            <button type="submit" className="dri-btn dri-btn-primary dri-btn-lg dri-auth-submit" disabled={submitting}>
              {submitting ? 'Un momento…' : register ? 'Crear mi cuenta' : 'Entrar'}
            </button>
          </form>

          <p className="dri-auth-switch">
            {register ? '¿Ya tienes una cuenta?' : '¿Aún no tienes cuenta?'}{' '}
            <Link href={switchHref}>{register ? 'Entrar' : 'Crear cuenta gratis'}</Link>
          </p>
        </div>
      </section>
    </main>
  )
}
