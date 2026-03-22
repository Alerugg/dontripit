import Link from 'next/link'
import FallbackImage from '../common/FallbackImage'
import { getPrintHref } from '../../lib/catalog/routes'

function variantLabel(print) {
  return [
    print.set_name || print.set_code,
    print.collector_number ? `Collector #${print.collector_number}` : null,
    print.language ? `Idioma ${print.language}` : null,
    print.finish ? `Finish ${print.finish}` : null,
    print.variant,
    print.rarity,
    print.year,
  ].filter(Boolean).join(' · ')
}

export default function VariantPicker({ prints }) {
  if (!prints?.length) return null

  return (
    <div className="variant-grid">
      {prints.map((print) => (
        <Link key={print.id} href={getPrintHref(print.id)} className="variant-card panel-soft">
          <div className="variant-thumb">
            <FallbackImage
              src={print.primary_image_url}
              alt={print.card?.name || print.set_name || 'Variante'}
              className="variant-thumb-image"
              placeholderClassName="image-fallback"
              label={print.language || print.variant || 'Variante'}
            />
          </div>
          <div className="variant-copy">
            <strong>{print.card?.name || 'Variante'}</strong>
            <small>{variantLabel(print) || 'Variante disponible'}</small>
          </div>
        </Link>
      ))}
    </div>
  )
}
