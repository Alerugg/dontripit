/** @type {import('next').NextConfig} */
const nextConfig = {
  turbopack: {
    root: __dirname,
  },
  outputFileTracingRoot: __dirname,
  images: {
    remotePatterns: [
      {
        protocol: 'https',
        hostname: 'cards.scryfall.io',
      },
      {
        protocol: 'https',
        hostname: 'en.onepiece-cardgame.com',
      },
    ],
  },
}

module.exports = nextConfig
