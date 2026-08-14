'use client'

import { useEffect } from 'react'

export default function PwaBootstrap() {
  useEffect(() => {
    if (!('serviceWorker' in navigator)) return undefined
    if (!window.isSecureContext) return undefined

    let cancelled = false

    async function register() {
      try {
        const registration = await navigator.serviceWorker.register('/sw.js', { scope: '/' })
        if (cancelled) return

        registration.update().catch(() => {})
      } catch (error) {
        if (process.env.NODE_ENV !== 'production') {
          console.warn('Don’tRipIt PWA service worker registration failed', error)
        }
      }
    }

    register()
    return () => { cancelled = true }
  }, [])

  return null
}
