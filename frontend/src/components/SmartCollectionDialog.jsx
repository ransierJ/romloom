import React from 'react'

const REGIONS = ['', 'USA', 'Europe', 'Japan', 'World', 'Asia', 'Korea', 'Brazil', 'Australia', 'China']

// Build / edit a smart collection: a name + a rule set that auto-populates
// membership. Rules AND together; multi-value fields (systems, genres) OR within.
export default function SmartCollectionDialog({ systems, initial, onClose, onSave }) {
  const r = initial?.rules || {}
  const [name, setName] = React.useState(initial?.name || '')
  const [sysIds, setSysIds] = React.useState(new Set(r.systems || []))
  const [genres, setGenres] = React.useState((r.genres || []).join(', '))
  const [coverage, setCoverage] = React.useState(r.coverage || '')
  const [region, setRegion] = React.useState(r.region || '')
  const [status, setStatus] = React.useState(r.status || '')
  const [favorite, setFavorite] = React.useState(!!r.favorite)
  const [minRating, setMinRating] = React.useState(r.min_rating ? String(r.min_rating) : '')
  const [search, setSearch] = React.useState(r.search || '')
  const [saving, setSaving] = React.useState(false)

  React.useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const toggleSys = (id) => setSysIds((s) => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n
  })

  const buildRules = () => {
    const rules = {}
    if (sysIds.size) rules.systems = [...sysIds]
    const gl = genres.split(',').map((x) => x.trim()).filter(Boolean)
    if (gl.length) rules.genres = gl
    if (coverage) rules.coverage = coverage
    if (region) rules.region = region
    if (status) rules.status = status
    if (favorite) rules.favorite = true
    if (minRating) rules.min_rating = Number(minRating)
    if (search.trim()) rules.search = search.trim()
    return rules
  }

  const save = async () => {
    if (!name.trim()) return
    setSaving(true)
    try { await onSave(name.trim(), buildRules(), initial?.id) }
    finally { setSaving(false) }
  }

  return (
    <div className="scrim" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>{initial?.id ? 'Edit smart collection' : 'New smart collection'}</h2>
          <button className="close" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="modal-body">
          <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
            Membership is computed from these rules and re-evaluated on refresh and after scans.
            Leave a rule blank to ignore it; all set rules must match.
          </p>

          <label className="efield">
            <span>Name</span>
            <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. USA RPGs I love" />
          </label>

          <div className="smart-grid">
            <label className="efield">
              <span>Genre(s) — comma-separated, any match</span>
              <input value={genres} onChange={(e) => setGenres(e.target.value)} placeholder="Shooter, Platform" />
            </label>
            <label className="efield">
              <span>Title contains</span>
              <input value={search} onChange={(e) => setSearch(e.target.value)} />
            </label>
            <label className="efield">
              <span>Region</span>
              <select className="ctl" value={region} onChange={(e) => setRegion(e.target.value)}>
                {REGIONS.map((x) => <option key={x} value={x}>{x || 'any'}</option>)}
              </select>
            </label>
            <label className="efield">
              <span>Coverage</span>
              <select className="ctl" value={coverage} onChange={(e) => setCoverage(e.target.value)}>
                <option value="">any</option>
                <option value="complete">complete</option>
                <option value="partial">partial</option>
                <option value="missing">missing</option>
              </select>
            </label>
            <label className="efield">
              <span>Completion status</span>
              <select className="ctl" value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="">any</option>
                <option value="playing">Playing</option>
                <option value="beaten">Beaten</option>
                <option value="completed">Completed</option>
                <option value="dropped">Dropped</option>
              </select>
            </label>
            <label className="efield">
              <span>Min. my rating</span>
              <select className="ctl" value={minRating} onChange={(e) => setMinRating(e.target.value)}>
                <option value="">any</option>
                {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}★ and up</option>)}
              </select>
            </label>
          </div>

          <label className="smart-check">
            <input type="checkbox" checked={favorite} onChange={(e) => setFavorite(e.target.checked)} />
            <span>Favorites only</span>
          </label>

          <div className="efield" style={{ marginTop: 12 }}>
            <span>Systems {sysIds.size > 0 && <em style={{ fontStyle: 'normal', color: 'var(--ink-faint)' }}>· {sysIds.size} selected</em>}</span>
            <div className="smart-systems">
              {systems.map((s) => (
                <button key={s.id} type="button"
                        className={`chip${sysIds.has(s.id) ? ' on' : ''}`}
                        onClick={() => toggleSys(s.id)}>{s.display_name}</button>
              ))}
              {systems.length === 0 && <span className="muted" style={{ fontSize: 12 }}>No systems indexed.</span>}
            </div>
            <span className="muted" style={{ fontSize: 11 }}>None selected = all systems.</span>
          </div>

          <div className="eactions" style={{ marginTop: 16 }}>
            <button className="btn sm ghost" onClick={onClose}>Cancel</button>
            <button className="btn sm solid" onClick={save} disabled={saving || !name.trim()}>
              {saving ? 'Saving…' : (initial?.id ? 'Save rules' : 'Create')}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
