import React from 'react'
import { api } from '../api.js'

// Region buckets offered by the Missing view — mirror the canonical regions the
// backend derives from No-Intro/Redump names. USA is the default.
const REGIONS = ['USA', 'Japan', 'Europe', 'World', 'Asia', 'Korea', 'Brazil', 'Australia']
const PAGE = 500

function fmtDate(epoch) {
  if (!epoch) return ''
  try { return new Date(epoch * 1000).toLocaleDateString() } catch { return '' }
}

// "What am I missing?" — diffs a system against its reference catalog.
export default function MissingView({ systemId, systemName, flash }) {
  const [region, setRegion] = React.useState('USA')
  const [search, setSearch] = React.useState('')
  const [dSearch, setDSearch] = React.useState('')
  const [data, setData] = React.useState(null)
  const [rows, setRows] = React.useState([])
  const [offset, setOffset] = React.useState(0)
  const [loading, setLoading] = React.useState(false)
  const [fetching, setFetching] = React.useState(false)

  React.useEffect(() => {
    const t = setTimeout(() => setDSearch(search), 250)
    return () => clearTimeout(t)
  }, [search])

  React.useEffect(() => { setOffset(0) }, [systemId, region, dSearch])

  React.useEffect(() => {
    if (!systemId) { setData(null); setRows([]); return }
    let alive = true
    setLoading(true)
    api.missing(systemId, { region, search: dSearch, limit: PAGE, offset })
      .then((res) => {
        if (!alive) return
        setData(res)
        setRows((prev) => (offset === 0 ? res.missing : [...prev, ...res.missing]))
      })
      .catch(() => { if (alive) { setData(null); setRows([]) } })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [systemId, region, dSearch, offset, fetching])

  const refresh = async () => {
    if (!systemId) return
    setFetching(true)
    try {
      await api.refreshCatalog(systemId)
      setOffset(0)
      setFetching((f) => !f)        // retrigger the fetch effect
      flash && flash(`Catalog updated for ${systemName || systemId}`)
    } catch {
      flash && flash('Could not fetch catalog — is the container online?')
    } finally {
      setFetching(false)
    }
  }

  if (!systemId) {
    return <div className="missing"><div className="empty">Pick a system on the left to see what you’re missing.</div></div>
  }

  const searchUrl = (title) =>
    `https://www.google.com/search?q=${encodeURIComponent(`${title} ${systemName || ''} rom`.trim())}`

  const hasMore = rows.length < (data?.missing_total || 0)

  const sentinelRef = React.useRef(null)
  React.useEffect(() => {
    if (!hasMore || loading) return
    const el = sentinelRef.current
    if (!el) return
    const io = new IntersectionObserver(
      (entries) => { if (entries[0].isIntersecting) setOffset((o) => o + PAGE) },
      { rootMargin: '800px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [hasMore, loading, rows.length])

  return (
    <div className="missing">
      <div className="toolbar">
        <h1>{systemName || systemId}</h1>
        {data?.imported && (
          <span className="count tnum">
            {data.have} / {data.catalog_total} {region} · <b>{data.missing_total} missing</b>
          </span>
        )}
        <div className="search">
          <span className="ic">⌕</span>
          <input placeholder="search missing titles…" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <select className="ctl" value={region} onChange={(e) => setRegion(e.target.value)} title="Region">
          {REGIONS.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <span style={{ flex: 1 }} />
        {data?.imported && (
          <span className="muted miss-src">
            {data.source} v{data.version} · {fmtDate(data.fetched_epoch)}
          </span>
        )}
        {data?.available && (
          <button className="btn sm ghost" onClick={refresh} disabled={fetching}>
            {fetching ? 'fetching…' : (data.imported ? '↻ refresh' : 'Fetch catalog')}
          </button>
        )}
      </div>

      <div className="content">
        {!data ? (
          <div className="empty">{loading ? 'Loading…' : 'No data.'}</div>
        ) : !data.available ? (
          <div className="empty miss-note">
            No reference catalog exists for <b>{systemName || systemId}</b>.<br />
            Arcade, Switch, and home-computer systems aren’t covered by No-Intro / Redump.
          </div>
        ) : !data.imported ? (
          <div className="empty miss-note">
            <p>The complete <b>{data.source === 'redump' ? 'Redump' : 'No-Intro'}</b> catalog for this system
               hasn’t been downloaded yet.</p>
            <button className="btn add-all" onClick={refresh} disabled={fetching}>
              {fetching ? 'Fetching…' : `Fetch ${region} catalog`}
            </button>
          </div>
        ) : rows.length === 0 ? (
          <div className="empty">
            {data.missing_total === 0
              ? `🎉 You have every ${region} title in the catalog.`
              : 'No missing titles match your search.'}
          </div>
        ) : (
          <>
            <ul className="miss-list">
              {rows.map((m, i) => (
                <li key={`${m.title}-${i}`}>
                  <span className="mt-title">{m.title}</span>
                  <span className="mt-regions">{m.regions}</span>
                  <a className="mt-search" href={searchUrl(m.title)}
                     target="_blank" rel="noopener noreferrer"
                     title={`Search Google for ${m.title}`}>⌕ search</a>
                </li>
              ))}
            </ul>
            {hasMore && (
              <div className="more" ref={sentinelRef}>
                <button onClick={() => setOffset(offset + PAGE)} disabled={loading}>
                  {loading ? `loading… (${rows.length} / ${data.missing_total})` : `load more (${rows.length} / ${data.missing_total})`}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
