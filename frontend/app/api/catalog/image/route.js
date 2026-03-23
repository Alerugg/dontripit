import { NextResponse } from 'next/server'

const ALLOWED_HOSTS = new Set([
  'en.onepiece-cardgame.com',
])

export async function GET(request) {
  const { searchParams } = new URL(request.url)
  const remoteSrc = searchParams.get('src') || ''

  let remoteUrl
  try {
    remoteUrl = new URL(remoteSrc)
  } catch {
    return NextResponse.json({ error: 'invalid_image_url' }, { status: 400 })
  }

  if (!ALLOWED_HOSTS.has(remoteUrl.hostname)) {
    return NextResponse.json({ error: 'image_host_not_allowed' }, { status: 400 })
  }

  const upstream = await fetch(remoteUrl.toString(), {
    cache: 'force-cache',
    next: { revalidate: 3600 },
  })

  if (!upstream.ok) {
    return NextResponse.json({ error: 'image_fetch_failed' }, { status: upstream.status })
  }

  const headers = new Headers()
  const contentType = upstream.headers.get('content-type')
  if (contentType) headers.set('content-type', contentType)
  headers.set('cache-control', 'public, s-maxage=3600, stale-while-revalidate=86400')

  return new NextResponse(upstream.body, {
    status: upstream.status,
    headers,
  })
}
