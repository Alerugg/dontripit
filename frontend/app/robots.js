const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://dontripit.com'

export default function robots() {
  return {
    rules: [
      {
        userAgent: '*',
        allow: '/',
        disallow: [
          '/api/',
          '/dashboard',
          '/collection',
          '/wishlist',
          '/console',
          '/profile',
        ],
      },
    ],
    sitemap: `${siteUrl}/sitemap.xml`,
    host: siteUrl,
  }
}
