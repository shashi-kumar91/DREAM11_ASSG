import type { ReactNode } from 'react'
import { useState } from 'react'

/** A photo with an honest caption: what it is, who took it, what licence it carries. */
export function Photo({
  src,
  alt,
  credit,
  ratio = '16 / 9',
  className = '',
  children,
}: {
  src: string
  alt: string
  credit?: { artist: string; license: string; source: string; title: string }
  ratio?: string
  className?: string
  children?: ReactNode
}) {
  const [failed, setFailed] = useState(false)
  return (
    <figure className={`photo ${className}`} style={{ aspectRatio: ratio }}>
      {!failed ? (
        <img src={src} alt={alt} loading="lazy" onError={() => setFailed(true)} />
      ) : (
        <div className="photo-fallback" aria-hidden="true">
          <span>🏏</span>
        </div>
      )}
      {children}
      {credit ? (
        <figcaption>
          {credit.title} · {credit.artist} · {credit.license}
        </figcaption>
      ) : null}
    </figure>
  )
}

/** A numbered step. The number is the whole point: lay readers need an order. */
export function Step({
  n,
  title,
  children,
  tone = 'red',
}: {
  n: number | string
  title: string
  children: ReactNode
  tone?: 'red' | 'teal' | 'amber' | 'violet'
}) {
  return (
    <div className={`step tone-${tone}`}>
      <span className="step-n">{n}</span>
      <div className="step-body">
        <h4>{title}</h4>
        <div className="step-text">{children}</div>
      </div>
    </div>
  )
}

export function Section({
  id,
  eyebrow,
  title,
  lead,
  children,
  tight = false,
}: {
  id?: string
  eyebrow?: string
  title?: string
  lead?: ReactNode
  children: ReactNode
  tight?: boolean
}) {
  return (
    <section id={id} className={`section${tight ? ' tight' : ''}`}>
      {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
      {title ? <h2 className="section-title">{title}</h2> : null}
      {lead ? <p className="section-lead">{lead}</p> : null}
      {children}
    </section>
  )
}

/** Fade-and-rise on mount, staggered by index. Pure CSS, no animation library. */
export function Reveal({
  children,
  delay = 0,
  as = 'div',
  className = '',
}: {
  children: ReactNode
  delay?: number
  as?: 'div' | 'li' | 'section'
  className?: string
}) {
  const Tag = as
  return (
    <Tag className={`reveal ${className}`} style={{ animationDelay: `${delay}ms` }}>
      {children}
    </Tag>
  )
}

export function Pill({
  children,
  tone = 'plain',
  title,
}: {
  children: ReactNode
  tone?: 'plain' | 'good' | 'warn' | 'bad' | 'info'
  title?: string
}) {
  return (
    <span className={`pill ${tone}`} title={title}>
      {children}
    </span>
  )
}

/** A disclosure used for everything an engineer wants and a fan does not. */
export function Drawer({
  label,
  hint,
  children,
  defaultOpen = false,
}: {
  label: string
  hint?: string
  children: ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className={`drawer${open ? ' open' : ''}`}>
      <button type="button" className="drawer-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="drawer-caret">{open ? '▾' : '▸'}</span>
        <span className="drawer-label">{label}</span>
        {hint ? <span className="drawer-hint">{hint}</span> : null}
      </button>
      {open ? <div className="drawer-body">{children}</div> : null}
    </div>
  )
}

export function Empty({ icon, title, sub }: { icon: string; title: string; sub?: string }) {
  return (
    <div className="empty-card">
      <div className="empty-icon">{icon}</div>
      <div>
        <b>{title}</b>
        {sub ? <p>{sub}</p> : null}
      </div>
    </div>
  )
}

export function ErrLines({ lines }: { lines: string[] }) {
  if (!lines.length) return null
  return (
    <div className="err-box" role="alert">
      {lines.map((l, i) => (
        <div key={i}>{l}</div>
      ))}
    </div>
  )
}
