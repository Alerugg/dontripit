import { NextResponse } from 'next/server'

function unauthorizedResponse() {
  return new NextResponse('Admin area requires authentication.', {
    status: 401,
    headers: {
      'WWW-Authenticate': 'Basic realm="Admin Console"',
    },
  })
}

function decodeBasicHeader(value) {
  try {
    return atob(value)
  } catch {
    return ''
  }
}

export function middleware(request) {
  const username = process.env.ADMIN_CONSOLE_USERNAME || ''
  const password = process.env.ADMIN_CONSOLE_PASSWORD || ''

  if (!username || !password) {
    return NextResponse.json(
      {
        error: 'admin_auth_not_configured',
        message: 'Admin authentication is not configured for this environment.',
      },
      { status: 503 },
    )
  }

  const authHeader = request.headers.get('authorization') || ''
  if (!authHeader.startsWith('Basic ')) {
    return unauthorizedResponse()
  }

  const encoded = authHeader.slice(6)
  const decoded = decodeBasicHeader(encoded)
  const separator = decoded.indexOf(':')

  if (separator < 0) {
    return unauthorizedResponse()
  }

  const providedUsername = decoded.slice(0, separator)
  const providedPassword = decoded.slice(separator + 1)

  if (providedUsername !== username || providedPassword !== password) {
    return unauthorizedResponse()
  }

  return NextResponse.next()
}

export const config = {
  matcher: ['/admin/:path*'],
}
