const CACHE_VERSION = 'dontripit-pwa-v2'
const OFFLINE_URL = '/offline.html'
const APP_SHELL = [
  OFFLINE_URL,
  '/manifest.webmanifest',
  '/icons/dontripit-app.svg',
  '/icons/dontripit-192.png',
  '/icons/dontripit-512.png',
]
const APP_SHELL_PATHS = new Set(APP_SHELL)

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION)
      .then((cache) => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_VERSION).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('message', (event) => {
  if (event.data?.type === 'SKIP_WAITING') self.skipWaiting()
})

self.addEventListener('fetch', (event) => {
  const request = event.request
  if (request.method !== 'GET') return

  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return

  // Never cache or replay authenticated/private API traffic.
  if (
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/dashboard') ||
    url.pathname.startsWith('/collection') ||
    url.pathname.startsWith('/login') ||
    url.pathname.startsWith('/register') ||
    url.pathname.startsWith('/forgot-password') ||
    url.pathname.startsWith('/reset-password')
  ) {
    return
  }

  // Only the tiny, non-user-specific PWA shell is cache-first.
  if (APP_SHELL_PATHS.has(url.pathname)) {
    event.respondWith(
      caches.open(CACHE_VERSION).then(async (cache) => {
        const cached = await cache.match(url.pathname)
        if (cached) return cached
        const response = await fetch(request)
        if (response.ok) cache.put(url.pathname, response.clone())
        return response
      }),
    )
    return
  }

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(async () => {
        const cache = await caches.open(CACHE_VERSION)
        return cache.match(OFFLINE_URL)
      }),
    )
  }
})
