import React from 'react'
import { mediaUrl } from '../api.js'

export const COVERAGE_LABEL = { complete: 'complete', partial: 'partial', missing: 'missing' }

export function CoveragePill({ value }) {
  return (
    <span className={`stat ${value}`}>
      <span className="d" />{COVERAGE_LABEL[value] || value}
    </span>
  )
}

// Human list of what's missing, for the gaps view.
export function GapFlags({ has }) {
  const gaps = []
  if (!has.cover) gaps.push(['no boxart', false])
  if (!has.screenshot) gaps.push(['no screenshot', false])
  if (!has.metadata) gaps.push(['no metadata', false])
  if (!has.wheel) gaps.push(['no wheel', true])
  if (!has.marquee) gaps.push(['no marquee', true])
  if (!has.video) gaps.push(['no video', true])
  if (gaps.length === 0) return <span className="stat complete"><span className="d" />complete</span>
  return (
    <>
      {gaps.map(([label, minor]) => (
        <span key={label} className={minor ? 'flag pa' : 'flag'}>{label}</span>
      ))}
    </>
  )
}

// An <img> that retries on error (with backoff + cache-bust) before giving up.
// The media exists — a failure is almost always a transient timeout while the
// backend resizes the source off a busy NAS — so retrying recovers it.
export function RetryImg({ src, alt = '', className, loading = 'lazy', maxRetries = 6, fallback = null }) {
  const [attempt, setAttempt] = React.useState(0)
  const [dead, setDead] = React.useState(false)
  React.useEffect(() => { setAttempt(0); setDead(false) }, [src])
  if (dead) return fallback
  const url = attempt ? `${src}${src.includes('?') ? '&' : '?'}r=${attempt}` : src
  return (
    <img
      className={className}
      src={url}
      alt={alt}
      loading={loading}
      onError={() => {
        if (attempt < maxRetries) {
          window.setTimeout(() => setAttempt((a) => a + 1), 400 * (attempt + 1))
        } else {
          setDead(true)
        }
      }}
    />
  )
}

// A single media thumbnail that gracefully shows a placeholder when absent.
export function Thumb({ gameId, asset, w = 200, wide = false, has = true, label }) {
  const placeholder = <span className="x">{label || 'none'}</span>
  return (
    <div className={`thumb${wide ? ' wide' : ''}`}>
      {has
        ? <RetryImg src={mediaUrl(gameId, asset, w)} fallback={placeholder} />
        : placeholder}
    </div>
  )
}

// Selection checkbox used across views when curating into a collection.
export function Check({ on, onToggle, title }) {
  return (
    <button
      type="button"
      className={`check${on ? ' on' : ''}`}
      title={title || (on ? 'Remove from collection' : 'Add to collection')}
      aria-pressed={on}
      onClick={(e) => { e.stopPropagation(); onToggle() }}
    >
      {on ? '✓' : '+'}
    </button>
  )
}

export function formatSize(bytes) {
  if (!bytes) return '—'
  const mb = bytes / 1048576
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`
  if (mb >= 1) return `${mb.toFixed(0)} MB`
  return `${(bytes / 1024).toFixed(0)} KB`
}
