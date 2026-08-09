'use client'

import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { Suspense, useState } from 'react'
import BrandMark from '../../components/brand/BrandMark'

function ResetPasswordForm() {
  const searchParams = useSearchParams()
  const token = searchParams.get('token') || ''
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function onSubmit(event) {
    event.preventDefault()
    setError('')
    setStatus('')
    const form = new FormData(event.currentTarget)
    const password = String(form.get('password') || '')
    const confirm = String(form.get('confirm_password') || '')
    if (password !== confirm) {
      setError('Las contraseñas no coinciden.')
      return
    }
    setSubmitting(true)
    try {
      const response = await fetch('/api/auth/reset-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, password }),
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.message || 'No pudimos cambiar tu contraseña.')
      setStatus(data.message || 'Contraseña actualizada. Ya puedes iniciar sesión.')
      event.currentTarget.reset()
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setSubmitting(false)
    }
  }

  if (!token) {
    return <p className="dri-auth-preview-note">Este enlace de recuperación no es válido.</p>
  }

  return (
    <form className="dri-auth-form" onSubmit={onSubmit}>
      <label>
        <span>Nueva contraseña</span>
        <input type="password" name="password" autoComplete="new-password" minLength={8} maxLength={200} required />
      </label>
      <label>
        <span>Repite la contraseña</span>
        <input type="password" name="confirm_password" autoComplete="new-password" minLength={8} maxLength={200} required />
      </label>
      {status ? <p className="dri-auth-success-note" role="status">{status}</p> : null}
      {error ? <p className="dri-auth-preview-note" role="alert">{error}</p> : null}
      <button type="submit" className="dri-btn dri-btn-primary dri-btn-lg" disabled={submitting || Boolean(status)}>
        {submitting ? 'Guardando…' : 'Guardar nueva contraseña'}
      </button>
      {status ? <Link href="/login" className="dri-btn dri-btn-ghost">Ir a iniciar sesión</Link> : null}
    </form>
  )
}

export default function ResetPasswordPage() {
  return (
    <main className="dri-auth-simple-page">
      <section className="dri-auth-simple-card">
        <Link href="/" aria-label="Don’tRipIt inicio"><BrandMark /></Link>
        <div>
          <span className="dri-kicker">Nueva contraseña</span>
          <h1>Recupera tu cuenta.</h1>
          <p>Elige una nueva contraseña. Al guardarla cerraremos las sesiones anteriores por seguridad.</p>
        </div>
        <Suspense fallback={<p>Cargando recuperación…</p>}>
          <ResetPasswordForm />
        </Suspense>
        <Link href="/login" className="dri-auth-back-link">← Volver a iniciar sesión</Link>
      </section>
    </main>
  )
}
