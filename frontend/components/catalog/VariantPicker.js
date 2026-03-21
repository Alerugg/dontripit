import Link from 'next/link'
import FallbackImage from '../common/FallbackImage'
import { getPrintHref } from '../../lib/catalog/routes'

function variantLabel(print) {
  return [
    print.collector_number ? `#${print.collector_number}` : null,
    print.variant || print.rarity,
    print.language,
  ].filter(Boolean).join(' · ')
}

export default function VariantPicker({ prints }) {
  if (!prints?.length) return null

  return (
    <div className="variant-grid">
      {prints.map((print) => (
        <Link key={print.id} href={getPrintHref(print.id)} className="variant-card">
          <div className="variant-thumb">
            <FallbackImage
              src={print.primary_image_url}
              alt={print.card?.name || print.set_name || 'Variante'}
              className="variant-thumb-image"
              placeholderClassName="image-fallback"
              label={print.language || print.variant || 'Variant'}
            />
          </div>
          <div className="variant-copy">
            <strong>{print.set_code || print.set_name || 'SET'}</strong>
            <small>{variantLabel(print) || 'Variante disponible'}</small>
          </div>
        </Link>
      ))}
    </div>
  )
}
