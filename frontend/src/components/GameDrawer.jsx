import React from 'react'
import { api, mediaUrl, videoUrl } from '../api.js'
import { CoveragePill, RetryImg, formatSize } from './bits.jsx'

const EDIT_FIELDS = [
  ['name', 'Name', 'input'],
  ['description', 'Description', 'textarea'],
  ['developer', 'Developer', 'input'],
  ['publisher', 'Publisher', 'input'],
  ['genre', 'Genre', 'input'],
  ['players', 'Players', 'input'],
  ['releasedate', 'Released (YYYY-MM-DD)', 'input'],
]

export default function GameDrawer({ gameId, onClose, onDeleted, onUpdated, onRescrape, canScrape, scrapeBusy }) {
  const [game, setGame] = React.useState(null)
  const [confirming, setConfirming] = React.useState(false)
  const [deleting, setDeleting] = React.useState(false)
  const [delError, setDelError] = React.useState(null)
  const [editing, setEditing] = React.useState(false)
  const [form, setForm] = React.useState({})
  const [saving, setSaving] = React.useState(false)
  const [editError, setEditError] = React.useState(null)
  const [uploading, setUploading] = React.useState(null)

  React.useEffect(() => {
    let alive = true
    setGame(null); setConfirming(false); setDeleting(false); setDelError(null)
    setEditing(false); setEditError(null); setUploading(null)
    api.game(gameId).then((g) => { if (alive) setGame(g) }).catch(() => {})
    return () => { alive = false }
  }, [gameId])

  const applyUpdate = (g) => { setGame(g); onUpdated && onUpdated(g) }

  const startEdit = () => {
    setForm({
      name: game.name || '', description: game.description || '',
      developer: game.developer || '', publisher: game.publisher || '',
      genre: game.genre || '', players: game.players || '',
      releasedate: game.releasedate || '',
    })
    setEditError(null)
    setEditing(true)
  }

  const saveEdit = async () => {
    setSaving(true); setEditError(null)
    try {
      const g = await api.editMetadata(game.id, form)
      applyUpdate(g)
      setEditing(false)
    } catch (e) {
      setEditError(/409/.test(e.message)
        ? 'Library is read-only — change /roms:ro to /roms (read-write) to save edits'
        : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const [scraping, setScraping] = React.useState(false)
  const rescrape = async () => {
    if (!onRescrape) return
    setScraping(true)
    try {
      const ok = await onRescrape(game)
      if (ok) applyUpdate(await api.game(game.id))
    } finally {
      setScraping(false)
    }
  }

  const uploadArt = async (asset, file) => {
    if (!file) return
    setUploading(asset); setEditError(null)
    try {
      applyUpdate(await api.uploadMedia(game.id, asset, file))
    } catch (e) {
      setEditError(e.status === 409
        ? 'Library is read-only — change /roms:ro to /roms (read-write) to add art'
        : 'Upload failed')
    } finally {
      setUploading(null)
    }
  }

  const doDelete = async () => {
    setDeleting(true); setDelError(null)
    try {
      const res = await api.deleteGame(game.id)
      onDeleted && onDeleted(game.id, res)
      onClose()
    } catch (e) {
      setDelError(e.message)
      setDeleting(false)
    }
  }

  React.useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const [videoFailed, setVideoFailed] = React.useState(false)
  React.useEffect(() => { setVideoFailed(false) }, [gameId])

  const g = game
  const showVideo = g && g.has.video && !videoFailed
  return (
    <div className="scrim" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="dhead">
          {showVideo ? (
            <video
              className="shot"
              src={videoUrl(g.id)}
              poster={g.has.screenshot ? mediaUrl(g.id, 'screenshot', 1024) : undefined}
              autoPlay muted loop playsInline controls
              onError={() => setVideoFailed(true)}
            />
          ) : g && g.has.screenshot ? (
            <RetryImg className="shot" src={mediaUrl(g.id, 'screenshot', 1024)} alt="" />
          ) : (
            <div className="shot" />
          )}
          <button className="close" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="dbody">
          {!g ? (
            <p className="mono" style={{ color: 'var(--ink-faint)' }}>loading…</p>
          ) : (
            <>
              {g.has.cover && (
                <RetryImg className="cover" src={mediaUrl(g.id, 'cover', 320)} alt="" />
              )}
              <div className="dtitle">
                <h2>{g.name}</h2>
                {!editing && canScrape && (
                  <button className="btn sm ghost" onClick={rescrape} disabled={scraping || scrapeBusy}
                          title="Re-scrape this game from ScreenScraper">
                    {scraping ? '⟳ scraping…' : '⟳ Rescrape'}
                  </button>
                )}
                {!editing && (
                  <button className="btn sm ghost" onClick={startEdit} title="Edit metadata & art">✎ Edit</button>
                )}
              </div>
              <div className="fn">{g.rom_filename}</div>
              <div style={{ marginTop: 10 }}><CoveragePill value={g.coverage} /></div>

              {editing ? (
                <div className="editform">
                  {EDIT_FIELDS.map(([key, label, kind]) => (
                    <label key={key} className="efield">
                      <span>{label}</span>
                      {kind === 'textarea'
                        ? <textarea rows={4} value={form[key]} onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))} />
                        : <input value={form[key]} onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))} />}
                    </label>
                  ))}

                  <div className="eart">
                    {['cover', 'screenshot'].map((a) => (
                      <label key={a} className="btn sm ghost art-btn">
                        {uploading === a ? `uploading ${a}…` : `${g.has[a] ? 'Replace' : 'Add'} ${a}…`}
                        <input type="file" accept="image/*" style={{ display: 'none' }}
                               disabled={uploading != null}
                               onChange={(e) => uploadArt(a, e.target.files?.[0])} />
                      </label>
                    ))}
                  </div>

                  {editError && <div className="del-error">{editError}</div>}
                  <div className="eactions">
                    <button className="btn sm ghost" onClick={() => setEditing(false)} disabled={saving}>Cancel</button>
                    <button className="btn sm solid" onClick={saveEdit} disabled={saving}>
                      {saving ? 'Saving…' : 'Save metadata'}
                    </button>
                  </div>
                  <p className="ehint">Writes a <code>textual/</code> XML and art into the library — survives rescans and flows into exports.</p>
                </div>
              ) : (
                <>
                  {g.description && <p className="desc">{g.description}</p>}
                  <dl className="facts">
                    {g.developer && (<><dt>Developer</dt><dd>{g.developer}</dd></>)}
                    {g.publisher && (<><dt>Publisher</dt><dd>{g.publisher}</dd></>)}
                    {g.genre && (<><dt>Genre</dt><dd>{g.genre}</dd></>)}
                    {g.releasedate && (<><dt>Released</dt><dd>{g.releasedate}</dd></>)}
                    {g.players && (<><dt>Players</dt><dd>{g.players}</dd></>)}
                    {g.rating != null && (<><dt>Rating</dt><dd>{Math.round(g.rating * 100)}%</dd></>)}
                    <dt>File</dt><dd>{g.ext?.toUpperCase()} · {formatSize(g.size_bytes)}</dd>
                  </dl>
                </>
              )}
              <div className="assets">
                {['cover', 'screenshot', 'wheel', 'marquee', 'video'].map((a) => (
                  <span key={a} className={g.has[a] ? 'flag pa' : 'flag'}
                        style={g.has[a] ? { background: 'var(--ok-wash)', color: 'var(--ok)' } : undefined}>
                    {g.has[a] ? `✓ ${a}` : `✕ ${a}`}
                  </span>
                ))}
                <span className="flag" style={g.has.metadata ? { background: 'var(--ok-wash)', color: 'var(--ok)' } : undefined}>
                  {g.has.metadata ? '✓ metadata' : '✕ metadata'}
                </span>
              </div>

              <div className="danger-zone">
                {!confirming ? (
                  <button className="btn sm danger" onClick={() => setConfirming(true)}>
                    Delete from disk…
                  </button>
                ) : (
                  <div className="del-confirm">
                    <p>Permanently delete <b>{g.name}</b> — the ROM file and its media.
                      This can’t be undone.</p>
                    <div className="del-actions">
                      <button className="btn sm ghost" onClick={() => setConfirming(false)} disabled={deleting}>Cancel</button>
                      <button className="btn sm danger solid" onClick={doDelete} disabled={deleting}>
                        {deleting ? 'Deleting…' : 'Delete permanently'}
                      </button>
                    </div>
                  </div>
                )}
                {delError && <div className="del-error">{delError}</div>}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
