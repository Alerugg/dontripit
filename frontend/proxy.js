import { NextResponse } from 'next/server'

const PUBLIC_PATHS = new Set([
  '/',
  '/login',
  '/register',
  '/forgot-password',
  '/reset-password',
  '/privacy',
  '/cookies',
  '/terms',
  '/pokemon',
  '/magic',
  '/onepiece',
  '/yugioh',
  '/riftbound',
  '/explorer',
])

const PUBLIC_PREFIXES = [
  '/games/',
  '/cards/',
  '/prints/',
  '/explorer/',
  '/tcg/',
  '/play/',
]

const PRIVATE_PREFIXES = [
  '/dashboard',
  '/collection',
  '/wishlist',
  '/console',
  '/profile',
]

function isPublicPath(pathname) {
  return PUBLIC_PATHS.has(pathname)
    || PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix))
}

function isPrivatePath(pathname) {
  return PRIVATE_PREFIXES.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`))
}

export function proxy(request) {
  const { pathname } = request.nextUrl
  const hasSession = Boolean(request.cookies.get('dri_session')?.value)

  if (hasSession && (pathname === '/login' || pathname === '/register')) {
    return NextResponse.redirect(new URL('/dashboard', request.url))
  }

  if (isPublicPath(pathname)) return NextResponse.next()

  if (!hasSession && isPrivatePath(pathname)) {
    const target = new URL('/register', request.url)
    target.searchParams.set('next', `${pathname}${request.nextUrl.search || ''}`)
    return NextResponse.redirect(target)
  }

  // Unknown routes must reach Next.js so it can return a real 404 instead of
  // masquerading as the registration page with a 200 response.
  return NextResponse.next()
}

export const config = {
  matcher: ['/((?!api|_next/static|_next/image|favicon.ico|.*\\..*).*)'],
}
