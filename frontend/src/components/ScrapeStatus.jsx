import React from 'react'
import { api } from '../api.js'

const LABEL = {
  running: 'Scraping', paused: 'Paused', done: 'Complete',
  stopped: 'Stopped', error: 'Failed', idle: 'Idle',
}

// Live scrape status + controls. `status` is polled by the parent; actions call
// the API and let the next poll reflect the new state.
export default function ScrapeStatus({ status, onClose, onRestart }) {
  const [busy, setBusy] = React.useState(false)
  const s = status || { state: 'idle' }
  const active = s.state === 'running' || s.state === 'paused'
  const pct = s.total > 0 ? Math.min(100, Math.round((s.current / s.total) * 100)) : null

  React.useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const act = async (fn) => {
    setBusy(true)
    try { await fn() } catch { /* a stale state; next poll corrects */ }
    finally { setBusy(false) }
  }

  const scopeText = s.scope === 'games'
    ? `${s.total || ''} selected game${s.total === 1 ? '' : 's'}`.trim()
    : 'whole system'

  return (
    <div className="scrim" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Scrape</h2>
          <button className="close" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="modal-body">
          <div className="scrape-head">
            <span className={`scrape-state ${s.state}`}>{LABEL[s.state] || s.state}</span>
            {s.system && <span className="muted">{s.system} · {scopeText}</span>}
          </div>

          <div className="pbar" aria-hidden="true">
            <i className={`fill ${s.state}`} style={{ width: pct == null ? '100%' : `${pct}%` }} />
          </div>
          <div className="pmeta tnum">
            {s.total > 0
              ? <>{s.current} / {s.total}{pct != null ? ` · ${pct}%` : ''}{s.phase ? ` · ${s.phase}` : ''}</>
              : <>{s.phase || s.message || '…'}</>}
          </div>

          {s.state === 'error' && <div className="del-error" style={{ marginTop: 12 }}>{s.error}</div>}
          {s.state === 'stopped' && <p className="muted" style={{ fontSize: 13 }}>Stopped — any media already scraped was kept.</p>}
          {s.state === 'paused' && <p className="muted" style={{ fontSize: 13 }}>Paused — the game mid-download may be re-fetched on resume.</p>}

          <div className="eactions" style={{ marginTop: 16 }}>
            {s.state === 'running' && (
              <button className="btn sm ghost" disabled={busy} onClick={() => act(api.scrapePause)}>⏸ Pause</button>
            )}
            {s.state === 'paused' && (
              <button className="btn sm ghost" disabled={busy} onClick={() => act(api.scrapeResume)}>▶ Resume</button>
            )}
            {active && (
              <button className="btn sm danger" disabled={busy} onClick={() => act(api.scrapeStop)}>⏹ Stop</button>
            )}
            {!active && s.request && (
              <button className="btn sm solid" disabled={busy} onClick={() => onRestart(s.request)}>↻ Restart</button>
            )}
            <button className="btn sm ghost" onClick={onClose}>Close</button>
          </div>
        </div>
      </div>
    </div>
  )
}
