import React from 'react'
import { api } from '../api.js'

const FIELDS = [
  { key: 'display_name', label: 'Display name' },
  { key: 'es_id', label: 'ES-DE system id' },
  { key: 'batocera_id', label: 'Batocera system id' },
  { key: 'retrobat_id', label: 'Retrobat system id' },
  { key: 'neostation_id', label: 'NeoStation system id' },
]

// Edit the per-system platform mapping used by export (folder -> frontend ids).
// Empty field = use the built-in default (shown as placeholder).
export default function MappingEditor({ systemId, onClose, onSaved }) {
  const [data, setData] = React.useState(null)
  const [form, setForm] = React.useState({})
  const [saving, setSaving] = React.useState(false)

  React.useEffect(() => {
    api.systemMapping(systemId).then((d) => {
      setData(d)
      setForm({ ...d.override })
    }).catch(() => {})
  }, [systemId])

  const save = async () => {
    setSaving(true)
    try {
      const d = await api.patchSystemMapping(systemId, form)
      setData(d); setForm({ ...d.override })
      onSaved && onSaved()
      onClose()
    } finally { setSaving(false) }
  }
  const reset = async () => {
    setSaving(true)
    try {
      const d = await api.patchSystemMapping(systemId, { display_name: '', es_id: '', batocera_id: '', retrobat_id: '', neostation_id: '' })
      setData(d); setForm({}); onSaved && onSaved()
    } finally { setSaving(false) }
  }

  return (
    <div className="scrim" onClick={onClose}>
      <div className="xdlg map-dlg" onClick={(e) => e.stopPropagation()}>
        <div className="xhead">
          <h2>System mapping</h2>
          <button className="close sm" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="xbody">
          {!data ? <p className="faint mono">loading…</p> : (
            <>
              <div className="map-folder">
                <span className="faint">source folder</span>
                <code>{data.folder}</code>
                {!data.default.known && <span className="flag">unmapped by default</span>}
              </div>
              <p className="faint" style={{ fontSize: 13, margin: '4px 0 14px' }}>
                These ids decide the export folder per frontend. Leave blank to use the built-in default.
              </p>
              {FIELDS.map((f) => (
                <label key={f.key} className="map-row">
                  <span className="map-label">{f.label}</span>
                  <input
                    value={form[f.key] ?? ''}
                    placeholder={data.default[f.key] || data.system_id}
                    onChange={(e) => setForm((s) => ({ ...s, [f.key]: e.target.value }))}
                  />
                  <span className="map-default">default: <code>{data.default[f.key] || data.system_id}</code></span>
                </label>
              ))}
              <div className="xactions">
                <button className="btn ghost" onClick={reset} disabled={saving}>Reset to defaults</button>
                <span style={{ flex: 1 }} />
                <button className="btn ghost" onClick={onClose}>Cancel</button>
                <button className="btn primary" onClick={save} disabled={saving}>Save</button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
