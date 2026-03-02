/** @type {import('next').NextConfig} */
const backendTarget =
  process.env.BACKEND_INTERNAL_URL ||
  process.env.BACKEND_URL ||
  'http://localhost:5000'

const nextConfig = {
  rewrites: async () => {
    return [
      {
        source: '/api/:path*',
        destination: `${backendTarget}/api/:path*`,
      },
    ]
  },
}

module.exports = nextConfig
