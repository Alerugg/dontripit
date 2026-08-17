import fs from 'node:fs'
import path from 'node:path'

const root = process.cwd()
const failures = []
const checks = []

function read(file) {
  const full = path.join(root, file)
  if (!fs.existsSync(full)) {
    failures.push(`missing:${file}`)
    return ''
  }
  return fs.readFileSync(full, 'utf8')
}

function requireTokens(file, tokens) {
  const body = read(file)
  const missing = tokens.filter((token) => !body.includes(token))
  checks.push({ file, required: tokens.length, missing })
  for (const token of missing) failures.push(`missing-token:${file}:${token}`)
  return body
}

const layout = requireTokens('app/layout.js', [
  'metadataBase:',
  'description:',
  'robots:',
  'openGraph:',
  'twitter:',
  'className="dri-skip-link"',
  'href="#main-content"',
  'id="main-content"',
  '<html lang="es">',
])

for (const [file, canonical] of [
  ['app/privacy/page.js', '/privacy'],
  ['app/terms/page.js', '/terms'],
  ['app/cookies/page.js', '/cookies'],
]) {
  requireTokens(file, [
    'export const metadata',
    `canonical: '${canonical}'`,
    '<h1>',
    'info@dontripit.com',
    '<SiteFooter />',
    '<TopNav />',
  ])
}

requireTokens('app/robots.js', [
  "userAgent: '*'",
  "'/api/'",
  "'/dashboard'",
  'sitemap:',
  'host:',
])
requireTokens('app/sitemap.js', [
  "['/', 1.0, 'daily']",
  "['/privacy', 0.3, 'monthly']",
  "['/terms', 0.3, 'monthly']",
  "['/cookies', 0.3, 'monthly']",
])

const cookiesPage = requireTokens('app/cookies/page.js', [
  'dri_session',
  'HttpOnly',
  'SameSite=Lax',
  'no instala actualmente cookies opcionales',
])
const session = requireTokens('lib/auth/serverSession.js', [
  "SESSION_COOKIE = 'dri_session'",
  'httpOnly: true',
  "sameSite: 'lax'",
  "secure: process.env.NODE_ENV === 'production'",
])

const pkg = JSON.parse(read('package.json') || '{}')
const dependencyNames = Object.keys({ ...(pkg.dependencies || {}), ...(pkg.devDependencies || {}) })
const optionalTrackingDependencies = dependencyNames.filter((name) =>
  /(analytics|segment|mixpanel|hotjar|clarity|gtag|google-analytics|facebook-pixel|posthog)/i.test(name),
)
checks.push({ cookie_tracking_dependencies: optionalTrackingDependencies })
if (optionalTrackingDependencies.length) {
  failures.push(`cookie-policy-tracking-dependencies:${optionalTrackingDependencies.join(',')}`)
}

requireTokens('components/layout/TopNav.js', [
  'aria-label="Navegación principal"',
  'aria-expanded={open}',
  "rel=\"noopener noreferrer\"",
  'type="button"',
])
requireTokens('components/layout/SiteFooter.js', [
  '<footer',
  'href="/privacy"',
  'href="/cookies"',
  'href="/terms"',
])
requireTokens('components/auth/AuthShell.js', [
  '<form',
  '<label>',
  'type="email"',
  'type="password"',
  'autoComplete=',
  'role="alert"',
  'type="submit"',
])
requireTokens('app/accessibility.css', [
  '.dri-skip-link',
  ':focus-visible',
  '@media (prefers-reduced-motion: reduce)',
])

// Critical image accessibility: every JSX <Image> or raw <img> opening tag in
// production app/components sources must carry an alt prop. This is intentionally
// narrow and deterministic rather than pretending to replace a browser a11y audit.
for (const base of ['app', 'components']) {
  const stack = [path.join(root, base)]
  while (stack.length) {
    const current = stack.pop()
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name)
      if (entry.isDirectory()) {
        stack.push(full)
        continue
      }
      if (!/\.(js|jsx|ts|tsx)$/.test(entry.name)) continue
      const body = fs.readFileSync(full, 'utf8')
      const tags = [...body.matchAll(/<(?:Image|img)\b[\s\S]*?>/g)]
      for (const match of tags) {
        if (!/\balt\s*=/.test(match[0])) {
          failures.push(`image-without-alt:${path.relative(root, full)}`)
        }
      }
      const blankTargets = [...body.matchAll(/<a\b[\s\S]*?target=["']_blank["'][\s\S]*?>/g)]
      for (const match of blankTargets) {
        if (!/\brel=["'][^"']*(noopener|noreferrer)/.test(match[0])) {
          failures.push(`blank-target-without-safe-rel:${path.relative(root, full)}`)
        }
      }
    }
  }
}

const report = {
  gate: failures.length ? 'FAIL' : 'PASS',
  failures,
  checks,
  assertions: {
    legal_pages: true,
    seo_metadata: Boolean(layout),
    essential_cookie_only_contract: Boolean(cookiesPage && session),
    accessibility_baseline: true,
  },
}

console.log(`POINT9_FRONTEND ${JSON.stringify(report, null, 2)}`)
if (failures.length) process.exit(1)
