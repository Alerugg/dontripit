'use client'

import Link from 'next/link'
import { useState } from 'react'
import BrandMark from '../../components/brand/BrandMark'

export default function ForgotPasswordPage() {
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function onSubmit(event) {
    event.preventDefault()
    setError('')
    setStatus('')
    setSubmitting(true)
    const form = new FormData(event.currentTarget)
    try {
      const response = await fetch('/api/auth/forgot-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: form.get('email') }),
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.message || 'No pudimos iniciar la recuperación.')
      setStatus(data.message || 'Si existe una cuenta con ese correo, recibirás un enlace para cambiar tu contraseña.')
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="dri-auth-simple-page">
      <section className="dri-auth-simple-card">
        <Link href="/" aria-label="Don’tRipIt inicio"><BrandMark /></Link>
        <div>
          <span className="dri-kicker">Recuperar acceso</span>
          <h1>Volvamos a entrar.</h1>
          <p>Escribe el correo de tu cuenta. Te enviaremos un enlace de un solo uso que caduca en 45 minutos.</p>
        </div>
        <form className="dri-auth-form" onSubmit={onSubmit}>
          <label>
            <span>Correo electrónico</span>
            <input type="email" name="email" autoComplete="email" placeholder="tu@email.com" maxLength={320} required />
          </label>
          {status ? <p className="dri-auth-success-note" role="status">{status}</p> : null}
          {error ? <p className="dri-auth-preview-note" role="alert">{error}</p> : null}
          <button type="submit" className="dri-btn dri-btn-primary dri-btn-lg" disabled={submitting}>
            {submitting ? 'Enviando…' : 'Enviar enlace de recuperación'}
          </button>
        </form>
        <Link href="/login" className="dri-auth-back-link">← Volver a iniciar sesión</Link>
      </section>
    </main>
  )
}
