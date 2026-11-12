import React from 'react'
import { api } from '../api.js'

// ScreenScraper login for built-in scraping. The password is write-only: the API
// returns only a "set" boolean, never the stored secret.
export default function SettingsDialog({ onClose, onSaved }) {
  const [user, setUser] = React.useState('')
  const [password, setPassword] = React.useState('')
  const [pwSet, setPwSet] = React.useState(false)
  const [region, setRegion] = React.useState('us')
  const [igdbId, setIgdbId] = React.useState('')
  const [igdbSecret, setIgdbSecret] = React.useState('')
  const [igdbSecretSet, setIgdbSecretSet] = React.useState(false)
  const [installed, setInstalled] = React.useState(true)
  const [saving, setSaving] = React.useState(false)
  const [msg, setMsg] = React.useState(null)

  React.useEffect(() => {
    api.getSettings().then((s) => {
      setUser(s.screenscraper?.user || '')
      setPwSet(!!s.screenscraper?.password_set)
      setRegion(s.scrape?.region || 'us')
      setIgdbId(s.igdb?.client_id || '')
      setIgdbSecretSet(!!s.igdb?.client_secret_set)
    }).catch(() => {})
    api.scrapeAvailable().then((a) => setInstalled(a.installed)).catch(() => {})
  }, [])

  React.useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const save = async () => {
    setSaving(true); setMsg(null)
    const body = { screenscraper: { user }, scrape: { region }, igdb: { client_id: igdbId } }
    if (password) body.screenscraper.password = password   // omit = keep existing
    if (igdbSecret) body.igdb.client_secret = igdbSecret
    try {
      await api.patchSettings(body)
      setMsg('Saved')
      setPassword(''); setPwSet(pwSet || !!password)
      onSaved && onSaved()
    } catch {
      setMsg('Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="scrim" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Settings</h2>
          <button className="close" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="modal-body">
          <h3>ScreenScraper login</h3>
          <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
            Used by the built-in scraper (Skyscraper). Your personal login raises the
            per-day quota and thread count. Create one at screenscraper.fr.
          </p>
          {!installed && (
            <div className="del-error" style={{ marginBottom: 12 }}>
              Skyscraper isn’t installed in this image — scraping is unavailable.
            </div>
          )}
          <label className="efield">
            <span>Username</span>
            <input value={user} onChange={(e) => setUser(e.target.value)} autoComplete="off" />
          </label>
          <label className="efield" style={{ marginTop: 12 }}>
            <span>Password {pwSet && <em style={{ color: 'var(--ink-faint)', fontStyle: 'normal' }}>· saved (leave blank to keep)</em>}</span>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                   placeholder={pwSet ? '••••••••' : ''} autoComplete="new-password" />
          </label>
          <label className="efield" style={{ marginTop: 12 }}>
            <span>Preferred region</span>
            <select className="ctl" value={region} onChange={(e) => setRegion(e.target.value)}>
              <option value="us">USA</option>
              <option value="eu">Europe</option>
              <option value="jp">Japan</option>
              <option value="wor">World</option>
            </select>
          </label>
          <p className="muted" style={{ fontSize: 12, marginTop: 6 }}>
            Which region’s names & box art to prefer (falls back to others if unavailable).
            USA avoids PAL names like “Starwing”/“Mega CD”.
          </p>

          <h3 style={{ marginTop: 22 }}>IGDB (optional)</h3>
          <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
            Supplies text metadata for Xbox / Xbox 360 / PS3 (which libretro-thumbnails
            doesn’t carry). Create a free app at dev.twitch.tv to get a Client ID + Secret.
          </p>
          <label className="efield">
            <span>Twitch Client ID</span>
            <input value={igdbId} onChange={(e) => setIgdbId(e.target.value)} autoComplete="off" />
          </label>
          <label className="efield" style={{ marginTop: 12 }}>
            <span>Client Secret {igdbSecretSet && <em style={{ color: 'var(--ink-faint)', fontStyle: 'normal' }}>· saved (leave blank to keep)</em>}</span>
            <input type="password" value={igdbSecret} onChange={(e) => setIgdbSecret(e.target.value)}
                   placeholder={igdbSecretSet ? '••••••••' : ''} autoComplete="new-password" />
          </label>

          <div className="eactions" style={{ marginTop: 16 }}>
            {msg && <span className="muted" style={{ marginRight: 'auto', fontSize: 13 }}>{msg}</span>}
            <button className="btn sm ghost" onClick={onClose}>Close</button>
            <button className="btn sm solid" onClick={save} disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
