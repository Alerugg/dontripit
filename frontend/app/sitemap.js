const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://dontripit.com'

const routes = [
  ['/', 1.0, 'daily'],
  ['/games/pokemon', 0.9, 'daily'],
  ['/games/magic', 0.9, 'daily'],
  ['/games/onepiece', 0.9, 'daily'],
  ['/games/yugioh', 0.9, 'daily'],
  ['/privacy', 0.3, 'monthly'],
  ['/terms', 0.3, 'monthly'],
  ['/cookies', 0.3, 'monthly'],
]

export default function sitemap() {
  const lastModified = new Date()
  return routes.map(([path, priority, changeFrequency]) => ({
    url: `${siteUrl}${path}`,
    lastModified,
    changeFrequency,
    priority,
  }))
}
