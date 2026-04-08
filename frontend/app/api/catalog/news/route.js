import { NextResponse } from 'next/server'
import { normalizeGameSlug } from '../../../../lib/catalog/games'
import { getNewsByGame } from '../../../../../backend/app/news/service.js'

export async function GET(request) {
  const { searchParams } = new URL(request.url)
  const game = normalizeGameSlug((searchParams.get('game') || '').trim().toLowerCase())
  const limit = Math.min(12, Math.max(1, Number(searchParams.get('limit') || 6)))
  const payload = await getNewsByGame(game, limit)

  return NextResponse.json(payload)
}
