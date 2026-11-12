import React from 'react'
import { api } from '../api.js'
import { formatSize } from './bits.jsx'

const TARGETS = [
  { id: 'raw', name: 'Raw / Universal', hint: 'Original folders + Skyscraper media & textual XML, verbatim.' },
  { id: 'es-de', name: 'ES-DE', hint: 'ROMs/<sys>/ + downloaded_media/<sys>/ + gamelist.xml (media auto-linked). Favorites in gamelist.' },
  { id: 'batocera', name: 'Batocera', hint: '<sys>/ + images/ + gamelist.xml. Also covers Knulli / ROCKNIX / AmberELEC (ES handhelds). Favorites in gamelist.' },
  { id: 'retrobat', name: 'Retrobat', hint: 'roms/<sys>/ + images/ + gamelist.xml (Windows ES). Favorites in gamelist.' },
  { id: 'muos', name: 'muOS', hint: 'ROMS/<sys>/ + MUOS/info/catalogue art + collection & Favourite .cfg. (handheld)' },
  { id: 'minui', name: 'MinUI / NextUI', hint: 'Roms/<Name> (TAG)/ + .res/ art + Collections/*.txt favorites. (handheld)' },
]
const ASSETS = ['cover', 'screenshot', 'wheel', 'marquee', 'video']
const MODES = [
  { id: 'copy', name: 'Copy', hint: 'Physical copies — portable to any device.' },
  { id: 'hardlink', name: 'Hardlink', hint: 'No extra space, same volume (falls back to copy across volumes).' },
  { id: 'symlink', name: 'Symlink', hint: 'No extra space — only for frontends reading this NAS.' },
]

export default function ExportDialog({ collectionId, detail, onClose }) {
  const [target, setTarget] = React.useState('es-de')
  const [assets, setAssets] = React.useState(new Set(ASSETS))
  const [mode, setMode] = React.useState('copy')
  const [preview, setPreview] = React.useState(null)
  const [phase, setPhase] = React.useState('config') // config | running | done | error
  const [progress, setProgress] = React.useState(null)
  const [busy, setBusy] = React.useState(false)
  const [presets, setPresets] = React.useState([])

  const body = () => ({ target, assets: [...assets], mode })

  const loadPresets = React.useCallback(() => {
    api.presets().then(setPresets).catch(() => {})
  }, [])
  React.useEffect(() => { loadPresets() }, [loadPresets])

  // Auto dry-run whenever target/assets change (while configuring).
  React.useEffect(() => {
    if (phase !== 'config') return
    let alive = true
    setBusy(true)
    api.exportCollection(collectionId, { target, assets: [...assets], dry_run: true })
      .then((r) => { if (alive) setPreview(r.plan) })
      .catch(() => { if (alive) setPreview(null) })
      .finally(() => { if (alive) setBusy(false) })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, assets, collectionId, phase])

  const toggleAsset = (a) => setAssets((s) => {
    const n = new Set(s); n.has(a) ? n.delete(a) : n.add(a); return n
  })

  const applyPreset = (p) => {
    if (!p) return
    setTarget(p.target)
    setAssets(new Set(p.assets))
    setMode(p.mode || 'copy')
  }
  const saveAsPreset = async () => {
    const name = window.prompt('Preset name (e.g. "RG35XX", "Living room ES-DE")')
    if (!name || !name.trim()) return
    await api.createPreset({ name: name.trim(), ...body() })
    loadPresets()
  }
  const removePreset = async (p, e) => {
    e.stopPropagation()
    if (!window.confirm(`Delete preset “${p.name}”?`)) return
    await api.deletePreset(p.id); loadPresets()
  }

  const start = async () => {
    setPhase('running')
    try {
      await api.exportCollection(collectionId, body())
      const poll = setInterval(async () => {
        const st = await api.exportStatus().catch(() => null)
        if (!st) return
        setProgress(st)
        if (st.state === 'done') { clearInterval(poll); setPhase('done') }
        if (st.state === 'error') { clearInterval(poll); setPhase('error') }
      }, 400)
    } catch {
      setPhase('error')
    }
  }

  const pct = progress ? progress.percent : 0

  return (
    <div className="scrim" onClick={onClose}>
      <div className="xdlg" onClick={(e) => e.stopPropagation()}>
        <div className="xhead">
          <h2>Export “{detail?.name}”</h2>
          <button className="close sm" onClick={onClose} aria-label="Close">✕</button>
        </div>

        {phase === 'config' && (
          <div className="xbody">
            {presets.length > 0 && (
              <>
                <div className="xsection-label">Presets</div>
                <div className="preset-row">
                  {presets.map((p) => (
                    <button key={p.id} className="preset" onClick={() => applyPreset(p)} title={`${p.target} · ${p.mode}`}>
                      {p.name}
                      <span className="x" onClick={(e) => removePreset(p, e)} title="Delete preset">✕</span>
                    </button>
                  ))}
                </div>
              </>
            )}

            <div className="xsection-label">Target frontend</div>
            <div className="target-grid">
              {TARGETS.map((t) => (
                <button key={t.id} className={`target${target === t.id ? ' on' : ''}`} onClick={() => setTarget(t.id)}>
                  <b>{t.name}</b>
                  <span>{t.hint}</span>
                </button>
              ))}
            </div>

            <div className="xsection-label">Include media</div>
            <div className="asset-row">
              {ASSETS.map((a) => (
                <label key={a} className={`asset${assets.has(a) ? ' on' : ''}`}>
                  <input type="checkbox" checked={assets.has(a)} onChange={() => toggleAsset(a)} />
                  {a}
                </label>
              ))}
            </div>

            <div className="xsection-label">Mode</div>
            <div className="target-grid mode-grid">
              {MODES.map((m) => (
                <button key={m.id} className={`target${mode === m.id ? ' on' : ''}`} onClick={() => setMode(m.id)}>
                  <b>{m.name}</b>
                  <span>{m.hint}</span>
                </button>
              ))}
            </div>

            <div className="xsection-label">Preview {busy && <span className="faint">· calculating…</span>}</div>
            {preview ? (
              <div className="preview">
                <div className="prow"><span>Games</span><b className="tnum">{preview.rom_count}</b></div>
                <div className="prow"><span>Media files</span><b className="tnum">{preview.media_count}</b></div>
                <div className="prow"><span>Gamelists</span><b className="tnum">{preview.gamelists}</b></div>
                <div className="prow">
                  <span>{mode === 'copy' ? 'Total size' : 'Size (linked — no extra disk)'}</span>
                  <b className="tnum">{formatSize(preview.total_bytes)}</b>
                </div>
                {preview.missing_rom_sources > 0 && (
                  <div className="prow warn"><span>ROMs not on disk (skipped)</span><b className="tnum">{preview.missing_rom_sources}</b></div>
                )}
                <div className="ppath"><span className="faint">writes to</span><code>{preview.output_path}</code></div>
              </div>
            ) : <div className="preview faint">—</div>}

            <div className="xactions">
              <button className="btn ghost" onClick={saveAsPreset} title="Save these settings as a reusable preset">Save as preset</button>
              <span style={{ flex: 1 }} />
              <button className="btn ghost" onClick={onClose}>Cancel</button>
              <button className="btn primary" onClick={start} disabled={!preview || preview.rom_count === 0}>
                Export {preview ? `${preview.rom_count} games` : ''} →
              </button>
            </div>
          </div>
        )}

        {(phase === 'running' || phase === 'done') && (
          <div className="xbody">
            <div className="xsection-label">{phase === 'done' ? 'Done' : 'Copying…'}</div>
            <div className="progress"><div className="bar" style={{ width: `${pct}%` }} /></div>
            <div className="pmeta tnum">
              {progress ? `${progress.files_done} / ${progress.files_total} files · ${formatSize(progress.bytes_done)} / ${formatSize(progress.bytes_total)} · ${pct}%` : 'starting…'}
            </div>
            {phase === 'done' && (
              <>
                <div className="done-msg">✓ {progress?.message || 'Export complete.'}</div>
                <div className="ppath"><span className="faint">at</span><code>{progress?.output_path}</code></div>
              </>
            )}
            <div className="xactions">
              <button className="btn primary" onClick={onClose} disabled={phase === 'running'}>
                {phase === 'running' ? 'Exporting…' : 'Close'}
              </button>
            </div>
          </div>
        )}

        {phase === 'error' && (
          <div className="xbody">
            <div className="done-msg err">✕ {progress?.message || 'Export failed.'}</div>
            <div className="xactions">
              <button className="btn" onClick={() => setPhase('config')}>Back</button>
              <button className="btn ghost" onClick={onClose}>Close</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
