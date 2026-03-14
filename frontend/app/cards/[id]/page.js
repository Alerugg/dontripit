'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import TopNav from '../../../components/layout/TopNav'
import StatePanel from '../../../components/catalog/StatePanel'
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

  return (
    <main>
      <TopNav />

      <section className="detail-shell">
        <Link href="/" className="back-link">← Volver al explorer</Link>

        {loading && <StatePanel title="Cargando carta" description="Preparando ficha maestra y variantes." />}
        {!loading && error && <StatePanel title="No pudimos cargar la carta" description={error} error />}

        {!loading && !error && card && (
          <article className="panel detail-page">
            <div className="detail-media">
              {card.primary_image_url ? (
                <img src={card.primary_image_url} alt={card.name} className="detail-image" />
              ) : (
                <div className="catalog-placeholder">Sin imagen</div>
              )}
            </div>

            <div className="detail-content">
              <p className="kicker">Ficha maestra</p>
              <h1>{card.name}</h1>
              <p className="meta-game">{card.game}</p>

              <section className="meta-grid panel-soft">
                <MetaItem label="Card ID" value={card.id} />
                <MetaItem label="Oracle ID" value={card.external_ids?.oracle_id} />
                <MetaItem label="Konami ID" value={card.external_ids?.konami_id} />
                <MetaItem label="TCGPlayer" value={card.external_ids?.tcgplayer_id} />
              </section>

              <section>
                <h2>Sets relacionados</h2>
                <div className="chip-list">
                  {(card.sets || []).map((setItem) => (
                    <span className="chip" key={setItem.id || setItem.code}>{setItem.code} · {setItem.name}</span>
                  ))}
                </div>
              </section>

              <section>
                <h2>Prints / Variantes</h2>
                <div className="prints-grid">
                  {(card.prints || []).map((print) => (
                    <Link key={print.id} href={`/prints/${print.id}`} className="print-row">
                      <div>
                        <strong>{print.set_code || 'SET'} #{print.collector_number || '-'}</strong>
                        <p>{print.variant || 'Standard'} · {print.rarity || 'Sin rarity'}</p>
                      </div>
                      <span>{print.language || 'N/A'}</span>
                    </Link>
                  ))}
                </div>
              </section>
            </div>
          </article>
        )}
      </section>
    </main>
  )
}
