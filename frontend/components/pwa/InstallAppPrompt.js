'use client'

import { useEffect, useState } from 'react'

function isStandalone() {
  if (typeof window === 'undefined') return false
  return window.matchMedia?.('(display-mode: standalone)').matches || window.navigator.standalone === true
}

function isAppleMobileWeb() {
  if (typeof window === 'undefined') return false
  const userAgent = window.navigator.userAgent || ''
  const platform = window.navigator.platform || ''
  const touchPoints = Number(window.navigator.maxTouchPoints || 0)
  const iosDevice = /iPhone|iPad|iPod/i.test(userAgent) || (platform === 'MacIntel' && touchPoints > 1)
  const safari = /Safari/i.test(userAgent) && !/CriOS|FxiOS|EdgiOS|OPiOS/i.test(userAgent)
  return iosDevice && safari
}

export default function InstallAppPrompt({ compact = false }) {
  const [installEvent, setInstallEvent] = useState(null)
  const [installed, setInstalled] = useState(false)
  const [iosSafari, setIosSafari] = useState(false)

  useEffect(() => {
    setInstalled(isStandalone())
    setIosSafari(isAppleMobileWeb())

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

  if (installed) return null

  async function install() {
    if (!installEvent) return
    try {
      await installEvent.prompt()
      const choice = await installEvent.userChoice
      if (choice?.outcome === 'accepted') setInstallEvent(null)
    } catch {
      // Browser owns the installation flow; a failed prompt should never break the page.
    }
  }

  if (installEvent) {
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

  if (!iosSafari) return null

  return (
    <details className={`pwa-ios-install ${compact ? 'is-compact' : ''}`}>
      <summary>Instalar en iPhone</summary>
      <div className="pwa-ios-install-card">
        <strong>Usar Don’tRipIt como app</strong>
        <ol>
          <li>En Safari, toca <b>Compartir</b>.</li>
          <li>Elige <b>Añadir a pantalla de inicio</b>.</li>
          <li>Activa <b>Abrir como app web</b> y toca <b>Añadir</b>.</li>
        </ol>
      </div>
    </details>
  )
}
