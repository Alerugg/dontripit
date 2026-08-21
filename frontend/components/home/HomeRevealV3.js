'use client'

import { useEffect, useRef, useState } from 'react'

export default function HomeRevealV3({ children, className = '', delay = 0, as = 'div', style, ...props }) {
  const ref = useRef(null)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const node = ref.current
    if (!node) return
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches || !('IntersectionObserver' in window)) {
      setVisible(true)
      return
    }

    const observer = new IntersectionObserver(([entry]) => {
      if (!entry?.isIntersecting) return
      setVisible(true)
      observer.disconnect()
    }, { threshold: 0.08, rootMargin: '0px 0px -10% 0px' })

    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  const Tag = as
  return (
    <Tag
      ref={ref}
      className={`v16-reveal ${visible ? 'is-visible' : ''} ${className}`.trim()}
      style={{ ...style, '--v16-delay': `${delay}ms` }}
      {...props}
    >
      {children}
    </Tag>
  )
}
