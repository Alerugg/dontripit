'use client'

import { useEffect, useState } from 'react'

function isStandalone() {
  if (typeof window === 'undefined') return false
  return window.matchMedia?.('(display-mode: standalone)').matches || window.navigator.standalone === true
}

export default function InstallAppPrompt({ compact = false }) {
  const [installEvent, setInstallEvent] = useState(null)
  const [installed, setInstalled] = useState(false)

  useEffect(() => {
    setInstalled(isStandalone())

    function handleBeforeInstallPrompt(event) {
      event.preventDefault()
      setInstallEvent(event)
    }

    function handleInstalled() {
      setInstalled(true)
      setInstallEvent(null)
    }

    window.addEventListener('beforeinstallprompt', handleBeforeInstallPrompt)
    window.addEventListener('appinstalled', handleInstalled)
    return () => {
      window.removeEventListener('beforeinstallprompt', handleBeforeInstallPrompt)
      window.removeEventListener('appinstalled', handleInstalled)
    }
  }, [])

  if (installed || !installEvent) return null

  async function install() {
    try {
      await installEvent.prompt()
      const choice = await installEvent.userChoice
      if (choice?.outcome === 'accepted') setInstallEvent(null)
    } catch {
      // Browser owns the installation flow; a failed prompt should never break the page.
    }
  }

  return (
    <button
      type="button"
      className={`pwa-install-action ${compact ? 'is-compact' : ''}`}
      onClick={install}
      aria-label="Instalar Don’tRipIt en este dispositivo"
    >
      <span aria-hidden="true">↓</span>
      Instalar Don’tRipIt
    </button>
  )
}
