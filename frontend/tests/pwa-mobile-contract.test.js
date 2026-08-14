const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
const source = (file) => fs.readFileSync(path.join(ROOT, file), 'utf8')

test('PWA manifest exposes standalone Don’tRipIt identity', () => {
  const manifest = JSON.parse(source('public/manifest.webmanifest'))
  assert.equal(manifest.name, 'Don’tRipIt')
  assert.equal(manifest.short_name, 'Don’tRipIt')
  assert.equal(manifest.display, 'standalone')
  assert.equal(manifest.scope, '/')
  assert.match(manifest.start_url, /^\//)
  assert.ok(manifest.icons.some((icon) => icon.src === '/icons/dontripit-192.png' && icon.sizes === '192x192'))
  assert.ok(manifest.icons.some((icon) => icon.src === '/icons/dontripit-512.png' && icon.sizes === '512x512'))
  assert.ok(manifest.icons.some((icon) => icon.src === '/icons/dontripit-512-maskable.png' && icon.purpose === 'maskable'))
  for (const icon of manifest.icons) {
    assert.ok(fs.existsSync(path.join(ROOT, 'public', icon.src.replace(/^\//, ''))), `missing icon ${icon.src}`)
  }
})

test('service worker never caches private account or API traffic', () => {
  const sw = source('public/sw.js')
  for (const route of ['/api/', '/dashboard', '/collection', '/login', '/register', '/forgot-password', '/reset-password']) {
    assert.match(sw, new RegExp(route.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  }
  assert.match(sw, /request\.mode === 'navigate'/)
  assert.match(sw, /offline\.html/)
  assert.doesNotMatch(sw, /caches\.put\(request/)
})

test('offline fallback is explicit about fresh data and privacy', () => {
  const offline = source('public/offline.html')
  assert.match(offline, /sin conexión/i)
  assert.match(offline, /precios, catálogo actualizado y datos de tu cuenta/i)
  assert.match(offline, /datos privados no se almacenan/i)
})

test('root layout wires manifest, standalone metadata and service worker bootstrap', () => {
  const layout = source('app/layout.js')
  const bootstrap = source('components/pwa/PwaBootstrap.js')
  assert.match(layout, /manifest: '\/manifest\.webmanifest'/)
  assert.match(layout, /appleWebApp/)
  assert.match(layout, /dontripit-192\.png/)
  assert.match(layout, /viewportFit: 'cover'/)
  assert.match(layout, /<PwaBootstrap \/>/)
  assert.match(bootstrap, /navigator\.serviceWorker\.register\('\/sw\.js'/)
  assert.match(bootstrap, /window\.isSecureContext/)
})

test('standalone CSS respects mobile safe areas and touch input sizing', () => {
  const css = source('app/pwa.css')
  assert.match(css, /safe-area-inset-top/)
  assert.match(css, /safe-area-inset-bottom/)
  assert.match(css, /display-mode: standalone/)
  assert.match(css, /max-width: 430px/)
  assert.match(css, /font-size: max\(16px, 1em\)/)
})
