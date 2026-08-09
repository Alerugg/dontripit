import { NextResponse } from 'next/server'

const PUBLIC_PATHS = new Set([
  '/',
  '/login',
  '/register',
  '/forgot-password',
  '/reset-password',
  '/privacy',
  '/terms',
])

export function proxy(request) {
  const { pathname } = request.nextUrl
  const hasSession = Boolean(request.cookies.get('dri_session')?.value)

  if (hasSession && (pathname === '/login' || pathname === '/register')) {
    return NextResponse.redirect(new URL('/dashboard', request.url))
  }

  if (!hasSession && !PUBLIC_PATHS.has(pathname)) {
    const target = new URL('/register', request.url)
    target.searchParams.set('next', `${pathname}${request.nextUrl.search || ''}`)
    return NextResponse.redirect(target)
  }

  return NextResponse.next()
}

export const config = {
  matcher: ['/((?!api|_next/static|_next/image|favicon.ico|.*\\..*).*)'],
}
