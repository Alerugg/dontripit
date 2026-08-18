import { NextResponse } from 'next/server'
import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../../lib/catalog/internalApi'

export async function GET(request, { params }) {
  const { id } = await params
  const locale = request.nextUrl.searchParams.get('locale') || ''
  const upstream = await callInternalApi(`/api/v1/prints/${id}`, {
    params: { locale },
    headers: locale ? { 'Accept-Language': locale } : {},
  })

  if (!upstream.ok) {
    const developerHint = getDeveloperErrorHint(upstream.payload, upstream.status)
    return NextResponse.json(
      {
        error: 'catalog_print_failed',
        message: getPublicErrorMessage(upstream.status),
        ...(developerHint ? { developer_hint: developerHint } : {}),
      },
      { status: upstream.status },
    )
  }

  return NextResponse.json(upstream.payload)
}