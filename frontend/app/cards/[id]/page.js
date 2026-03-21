'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import TopNav from '../../../components/layout/TopNav'
import FallbackImage from '../../../components/common/FallbackImage'
import StatePanel from '../../../components/catalog/StatePanel'
import VariantPicker from '../../../components/catalog/VariantPicker'
import { fetchCardById } from '../../../lib/catalog/client'

function MetaItem({ label, value }) {
  if (!value) return null
  return <p><strong>{label}:</strong> {value}</p>
}

export default function CardDetailPage({ params }) {
  const [card, setCard] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false

    async function loadCard() {
      setLoading(true)
      setError('')

      try {
        const payload = await fetchCardById(params.id)
        if (!cancelled) setCard(payload)
      } catch (requestError) {
        if (!cancelled) {
          setCard(null)
          setError(requestError.message)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    loadCard()
    return () => {
      cancelled = true
    }
  }, [params.id])

  const primarySet = useMemo(() => card?.sets?.[0] || null, [card])

  return (
    <main>
      <TopNav />

      <section className="detail-shell">
        <Link href={card?.game ? `/tcg/${card.game}` : '/explorer'} className="back-link">← Volver al explorer</Link>

        {loading && <StatePanel title="Cargando carta" description="Preparando carta, colecciones y variantes." />}
        {!loading && error && <StatePanel title="No pudimos cargar la carta" description={error} error />}

        {!loading && !error && card && (
          <article className="panel detail-page detail-card-page">
            <div className="detail-media">
              <FallbackImage
                src={card.primary_image_url}
                alt={card.name}
                className="detail-image"
                placeholderClassName="catalog-placeholder image-fallback"
                label={card.game || 'TCG'}
              />
            </div>

            <div className="detail-content">
              <nav className="detail-breadcrumbs" aria-label="breadcrumb">
                <Link href={card.game ? `/tcg/${card.game}` : '/explorer'}>{card.game || 'TCG'}</Link>
                <span>→</span>
                <span>{primarySet?.name || primarySet?.code || card.language || 'Colección'}</span>
                <span>→</span>
                <strong>Carta</strong>
              </nav>

              <p className="kicker">Carta</p>
              <h1>{card.name}</h1>
              <p className="meta-subtitle detail-intro">{primarySet?.name || 'Colección principal'}{card.language ? ` · ${card.language}` : ''}</p>

              <section className="meta-grid panel-soft">
                <MetaItem label="Card ID" value={card.id} />
                <MetaItem label="Oracle ID" value={card.external_ids?.oracle_id} />
                <MetaItem label="Konami ID" value={card.external_ids?.konami_id} />
                <MetaItem label="TCGPlayer" value={card.external_ids?.tcgplayer_id} />
              </section>

              <section>
                <h2>Colecciones</h2>
                <div className="chip-list">
                  {(card.sets || []).map((setItem) => (
                    <span className="chip" key={setItem.id || setItem.code}>{setItem.code} · {setItem.name}</span>
                  ))}
                </div>
              </section>

              <section>
                <div className="section-head">
                  <div>
                    <h2>Variantes</h2>
                    <p>Miniaturas de cada print para comparar collector number, rareza, variante e idioma.</p>
                  </div>
                </div>
                <VariantPicker prints={card.prints || []} />
              </section>
            </div>
          </article>
        )}
      </section>
    </main>
  )
}
