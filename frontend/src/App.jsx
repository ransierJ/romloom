import React from 'react'
import { api, gameRef } from './api.js'
import { ListView, DetailsView, BoxartView, GapsView } from './components/Views.jsx'
import { CuratePicker, CollectionHeader } from './components/Collections.jsx'
import GameDrawer from './components/GameDrawer.jsx'
import ExportDialog from './components/ExportDialog.jsx'
import MappingEditor from './components/MappingEditor.jsx'
import MissingView from './components/MissingView.jsx'
import SettingsDialog from './components/SettingsDialog.jsx'
import ScrapeStatus from './components/ScrapeStatus.jsx'
import SmartCollectionDialog from './components/SmartCollectionDialog.jsx'

const VIEWS = ['List', 'Details', 'Boxart', 'Gaps']
const PAGE = 100
// Region filter options — must mirror the keys of config.REGION_TOKENS.
const REGIONS = ['USA', 'Europe', 'Japan', 'World', 'Asia', 'Korea', 'Brazil', 'Australia', 'China']

function useDebounced(value, ms) {
  const [v, setV] = React.useState(value)
  React.useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

const initial = new URLSearchParams(window.location.search)

export default function App() {
  const [mode, setMode] = React.useState(
    ['collections', 'missing'].includes(initial.get('mode')) ? initial.get('mode') : 'library')
  const [systems, setSystems] = React.useState([])
  const [active, setActive] = React.useState(initial.get('system') || null)
  const [view, setView] = React.useState(VIEWS.includes(initial.get('view')) ? initial.get('view') : 'Boxart')
  const [search, setSearch] = React.useState('')
  const [coverage, setCoverage] = React.useState('')
  const [region, setRegion] = React.useState('')
  const [genre, setGenre] = React.useState('')
  const [onlyFav, setOnlyFav] = React.useState(false)
  const [genreList, setGenreList] = React.useState([])
  const [sort, setSort] = React.useState('name')

  const [collections, setCollections] = React.useState([])
  const [curateInto, setCurateInto] = React.useState(initial.get('curate') ? Number(initial.get('curate')) : null)
  const [activeCollection, setActiveCollection] = React.useState(initial.get('collection') ? Number(initial.get('collection')) : null)
  const [collDetail, setCollDetail] = React.useState(null)
  const [collSystem, setCollSystem] = React.useState(null)   // filter a collection to one system
  const [smartDialog, setSmartDialog] = React.useState(null) // null | {} (new) | {id,name,rules} (edit)

  const [games, setGames] = React.useState([])
  const [total, setTotal] = React.useState(0)
  const [offset, setOffset] = React.useState(0)
  const [loading, setLoading] = React.useState(false)
  const [openId, setOpenId] = React.useState(initial.get('game') ? Number(initial.get('game')) : null)
  const [scan, setScan] = React.useState(null)
  const [toast, setToast] = React.useState(null)
  const [reloadKey, setReloadKey] = React.useState(0)
  const [exportOpen, setExportOpen] = React.useState(initial.get('export') === '1')
  const [mapOpen, setMapOpen] = React.useState(initial.get('mapping') === '1')
  const [selectMode, setSelectMode] = React.useState(initial.get('select') === '1')
  const [selectedIds, setSelectedIds] = React.useState(() => new Set())
  const [settingsOpen, setSettingsOpen] = React.useState(false)
  const [scrapeInfo, setScrapeInfo] = React.useState(null)   // {installed, credentials_set, platform}
  const [scrapeStatus, setScrapeStatus] = React.useState(null)
  const [scrapeModal, setScrapeModal] = React.useState(false)
  const pollingRef = React.useRef(false)
  const waitersRef = React.useRef([])
  const forceReload = () => { setOffset(0); setReloadKey((k) => k + 1) }

  const toggleSelected = (id) => setSelectedIds((s) => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n
  })
  const exitSelect = () => { setSelectMode(false); setSelectedIds(new Set()) }
  // Leave select mode when the system/collection changes (but not on first mount).
  const selFirst = React.useRef(true)
  React.useEffect(() => {
    if (selFirst.current) { selFirst.current = false; return }
    exitSelect()
  }, [mode, active, activeCollection])

  const dSearch = useDebounced(search, 250)

  const flash = (msg) => { setToast(msg); setTimeout(() => setToast(null), 2600) }

  const loadSystems = React.useCallback(() => {
    api.systems().then((s) => {
      setSystems(s)
      setActive((cur) => cur || (s[0] && s[0].id) || null)
    }).catch(() => {})
  }, [])
  const loadCollections = React.useCallback(() => {
    api.collections().then(setCollections).catch(() => {})
  }, [])

  React.useEffect(() => { loadSystems(); loadCollections() }, [loadSystems, loadCollections])
  React.useEffect(() => {
    const poll = () => api.scanStatus().then(setScan).catch(() => {})
    poll(); const t = setInterval(poll, 4000); return () => clearInterval(t)
  }, [])

  // Reset paging on any query change.
  React.useEffect(() => { setOffset(0) }, [mode, active, activeCollection, dSearch, coverage, region, genre, onlyFav, sort, curateInto, collSystem])

  // A system filter only applies within one collection — clear it on switch.
  React.useEffect(() => { setCollSystem(null) }, [activeCollection, mode])

  // Changing system clears region/genre filters that may not apply, and loads
  // the new system's genre facet for the dropdown.
  React.useEffect(() => {
    setRegion(''); setGenre('')
    if (active) api.genres(active).then(setGenreList).catch(() => setGenreList([]))
    else setGenreList([])
  }, [active])

  // Fetch a page for whichever mode is active.
  React.useEffect(() => {
    if (mode === 'missing') return   // Missing view fetches its own data
    let alive = true
    const opts = { search: dSearch, coverage, region: region || null, genre: genre || null, favorite: onlyFav, sort, limit: PAGE, offset }
    let req
    if (mode === 'collections') {
      if (!activeCollection) { setGames([]); setTotal(0); return }
      req = api.collectionGames(activeCollection, { ...opts, system: collSystem || '' })
    } else {
      if (!active) { setGames([]); setTotal(0); return }
      req = api.games(active, { ...opts, selectedFor: curateInto })
    }
    setLoading(true)
    req.then((res) => {
      if (!alive) return
      setTotal(res.total)
      setGames((prev) => (offset === 0 ? res.games : [...prev, ...res.games]))
    }).catch(() => { if (alive) { setGames([]); setTotal(0) } })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [mode, active, activeCollection, dSearch, coverage, region, genre, onlyFav, sort, offset, curateInto, collSystem, reloadKey])

  // Collection detail for the Collections page header.
  React.useEffect(() => {
    if (mode === 'collections' && activeCollection) {
      api.collection(activeCollection).then(setCollDetail).catch(() => setCollDetail(null))
    }
  }, [mode, activeCollection])

  // Refresh after a scan completes.
  const prevState = React.useRef()
  React.useEffect(() => {
    if (prevState.current === 'running' && scan && scan.state === 'idle') { loadSystems(); setOffset(0) }
    prevState.current = scan && scan.state
  }, [scan, loadSystems])

  const activeSys = systems.find((s) => s.id === active)
  const View = { List: ListView, Details: DetailsView, Boxart: BoxartView, Gaps: GapsView }[view]
  const hasMore = games.length < total

  // Infinite scroll: auto-load the next page when a sentinel nears the viewport,
  // so a whole system streams in as you scroll (images are lazy, so only the
  // visible covers actually fetch). The button below stays as a manual fallback.
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
  }, [hasMore, loading, games.length])

  // ---- collection mutations ----
  const newCollection = async () => {
    const name = window.prompt('Name this collection (e.g. "RG35XX", "Living room")')
    if (!name || !name.trim()) return
    const c = await api.createCollection(name.trim())
    await loadCollections()
    setCurateInto(c.id)
    flash(`Created “${c.name}” — now curating into it`)
  }

  const toggleMembership = async (g) => {
    const on = g.selected
    setGames((gs) => gs.map((x) => (x.id === g.id ? { ...x, selected: !on } : x)))
    try {
      if (on) await api.removeGames(curateInto, [gameRef(g)])
      else await api.addGames(curateInto, [gameRef(g)])
      loadCollections()
    } catch {
      setGames((gs) => gs.map((x) => (x.id === g.id ? { ...x, selected: on } : x))) // revert
    }
  }

  const removeFromCollection = async (g) => {
    setGames((gs) => gs.filter((x) => x.id !== g.id))
    setTotal((t) => t - 1)
    try {
      const d = await api.removeGames(activeCollection, [gameRef(g)])
      setCollDetail(d); loadCollections()
    } catch { /* leave as is; next load corrects */ }
  }

  const bulkAddFiltered = async () => {
    const label = collections.find((c) => c.id === curateInto)?.name || 'collection'
    const n = total
    const d = await api.addByQuery(curateInto, { system_id: active, search: dSearch, coverage: coverage || null })
    forceReload()          // re-fetch to light up the checkboxes
    loadCollections()
    flash(`Added ${n} to “${label}” — ${d.game_count} games total`)
  }

  const saveSmart = async (name, rules, editId) => {
    try {
      const d = editId
        ? await api.patchCollection(editId, { name, rules })
        : await api.createCollection(name, null, rules)
      await loadCollections()
      setSmartDialog(null)
      setMode('collections'); setActiveCollection(d.id); setCollDetail(d)
      if (editId) forceReload()
      flash(editId ? `Updated “${d.name}” — ${d.game_count} games`
        : `Created “${d.name}” — ${d.game_count} games`)
    } catch { flash('Save failed') }
  }

  const renameCollection = async (name) => {
    if (!name) return
    const d = await api.patchCollection(activeCollection, { name }); setCollDetail(d); loadCollections()
  }
  const deleteCollection = async () => {
    if (!window.confirm(`Delete “${collDetail.name}”? The ROMs are untouched — only the collection is removed.`)) return
    await api.deleteCollection(activeCollection)
    setActiveCollection(null); setCollDetail(null); loadCollections(); setMode('library')
  }
  const refreshCollectionMeta = async () => {
    try {
      const d = await api.refreshCollection(activeCollection)
      setCollDetail(d); forceReload(); loadCollections()
      flash(`Refreshed metadata for ${d.updated} game${d.updated === 1 ? '' : 's'}`)
    } catch { flash('Refresh failed') }
  }
  const pruneCollectionDangling = async () => {
    const n = collDetail.game_count - collDetail.resolved
    if (n <= 0) return
    if (!window.confirm(`Remove ${n} unresolved item${n === 1 ? '' : 's'} (ROMs no longer in the library) from “${collDetail.name}”? The ROMs themselves aren’t touched.`)) return
    try {
      const d = await api.pruneCollection(activeCollection)
      setCollDetail(d); loadCollections(); forceReload()
      flash(`Removed ${d.removed} unresolved item${d.removed === 1 ? '' : 's'}`)
    } catch { flash('Clean failed') }
  }

  const bulkDeleteSelected = async () => {
    const ids = [...selectedIds]
    if (!ids.length) return
    if (!window.confirm(`Permanently delete ${ids.length} game${ids.length > 1 ? 's' : ''} and their media from disk? This cannot be undone.`)) return
    try {
      const res = await api.bulkDelete(ids)
      setGames((gs) => gs.filter((g) => !selectedIds.has(g.id)))
      setTotal((t) => Math.max(0, t - res.deleted_count))
      loadSystems()
      if (mode === 'collections' && activeCollection) api.collection(activeCollection).then(setCollDetail).catch(() => {})
      flash(`Deleted ${res.deleted_count} from disk`)
      exitSelect()
    } catch (e) {
      flash(e.status === 409 ? 'Library is read-only — make the mount writable to delete' : 'Delete failed')
    }
  }

  const bulkAddToCollection = async (cid) => {
    // Send ids, not loaded rows: a selection can span pages that aren't all in
    // the `games` array, and the backend resolves the natural key per id.
    const ids = [...selectedIds]
    if (!ids.length) return
    const label = collections.find((c) => c.id === cid)?.name || 'collection'
    try {
      const d = await api.addGamesByIds(cid, ids)
      loadCollections()
      flash(`Added ${ids.length} to “${label}”${d && d.game_count != null ? ` — ${d.game_count} games total` : ''}`)
      exitSelect()
    } catch {
      flash('Add to collection failed')
    }
  }

  const bulkAddToNewCollection = async () => {
    const name = window.prompt('Name this collection (e.g. "RG35XX", "Living room")')
    if (!name || !name.trim()) return
    const c = await api.createCollection(name.trim())
    await loadCollections()
    await bulkAddToCollection(c.id)
  }

  // Which scraper options are available for the active system.
  React.useEffect(() => {
    if (!active) { setScrapeInfo(null); return }
    let alive = true
    api.scrapeAvailable(active).then((a) => { if (alive) setScrapeInfo(a) }).catch(() => {})
    return () => { alive = false }
  }, [active, settingsOpen])

  // One poll loop drives the status object + the banner/modal; terminal states
  // refresh the view and resolve any awaiting callers.
  const beginPolling = () => {
    if (pollingRef.current) return
    pollingRef.current = true
    const tick = async () => {
      let st
      try { st = await api.scrapeStatus() } catch { setTimeout(tick, 1500); return }
      setScrapeStatus(st)
      if (st.state === 'running' || st.state === 'paused') { setTimeout(tick, 1500); return }
      pollingRef.current = false
      if (st.state === 'done' || st.state === 'stopped') { forceReload(); loadSystems() }
      const waiters = waitersRef.current; waitersRef.current = []
      waiters.forEach((r) => r(st.state === 'done'))
    }
    tick()
  }

  // Resume polling if a scrape is already running when the app loads.
  React.useEffect(() => {
    api.scrapeStatus().then((st) => {
      if (st.state === 'running' || st.state === 'paused') { setScrapeStatus(st); beginPolling() }
    }).catch(() => {})
  }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  const runScrape = async (payload) => {
    try {
      await api.startScrape(payload)
    } catch (e) {
      flash(/409/.test(e.message) ? 'A scrape is already running'
        : 'Set your ScreenScraper login in Settings first')
      if (/400|401/.test(e.message)) setSettingsOpen(true)
      return false
    }
    setScrapeModal(true)
    return new Promise((resolve) => { waitersRef.current.push(resolve); beginPolling() })
  }

  const scrapeSystem = async () => {
    if (!activeSys) return
    if (await runScrape({ system_id: active })) flash(`Scraped ${activeSys.display_name}`)
  }

  const scrapeSelected = async () => {
    const ids = [...selectedIds]
    if (!ids.length) return
    if (await runScrape({ system_id: active, game_ids: ids })) { exitSelect(); flash(`Scraped ${ids.length} games`) }
  }

  const restartScrape = (request) => { runScrape(request) }

  const canScrape = scrapeInfo && scrapeInfo.installed && scrapeInfo.platform
  const scrapeBusy = scrapeStatus && (scrapeStatus.state === 'running' || scrapeStatus.state === 'paused')

  // Select mode (for bulk delete) takes over the checkboxes when active.
  const sel = selectMode
    ? { active: true, isOn: (g) => selectedIds.has(g.id), toggle: (g) => toggleSelected(g.id) }
    : (mode === 'collections'
        ? { active: true, isOn: () => true, toggle: removeFromCollection }
        : (curateInto != null
            ? { active: true, isOn: (g) => !!g.selected, toggle: toggleMembership }
            : null))

  const openGame = (g) => (selectMode ? toggleSelected(g.id) : setOpenId(g.id))

  const toggleFavorite = async (g) => {
    const next = !(g.user && g.user.favorite)
    const apply = (fav) => setGames((gs) => gs.map((x) => (x.id === g.id
      ? { ...x, user: { ...(x.user || {}), favorite: fav } } : x)))
    apply(next)
    try { await api.setUserData(g.id, { favorite: next }) } catch { apply(!next) }
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="mark">🕹</span><b>ROMFORGE</b><span className="ver">v0.9.0</span>
        </div>
        <div className="tabs">
          <button className={mode === 'library' ? 'on' : ''} onClick={() => setMode('library')}>Systems</button>
          <button className={mode === 'collections' ? 'on' : ''} onClick={() => setMode('collections')}>
            Collections{collections.length ? ` (${collections.length})` : ''}
          </button>
          <button className={mode === 'missing' ? 'on' : ''} onClick={() => setMode('missing')}>Missing</button>
        </div>

        {mode !== 'collections' ? (
          <nav className="sys-list">
            {systems.map((s) => {
              const pct = s.rom_count ? (100 * s.complete_count / s.rom_count) : 0
              return (
                <button key={s.id} className={`sys${s.id === active ? ' active' : ''}`} onClick={() => setActive(s.id)}>
                  <span className="row1"><span className="nm">{s.display_name}</span><span className="ct tnum">{s.rom_count}</span></span>
                  <span className="covbar"><i className="c" style={{ width: `${pct}%` }} /><i className="m" style={{ width: `${100 - pct}%` }} /></span>
                </button>
              )
            })}
            {systems.length === 0 && <p className="side-empty">No systems indexed. Run a scan →</p>}
          </nav>
        ) : (
          <nav className="sys-list">
            {collections.map((c) => (
              <button key={c.id} className={`sys${c.id === activeCollection ? ' active' : ''}`} onClick={() => setActiveCollection(c.id)}>
                <span className="row1"><span className="nm">{c.smart && <span className="smart-ic" title="Smart collection">⚡</span>}{c.name}</span><span className="ct tnum">{c.game_count}</span></span>
              </button>
            ))}
            <button className="newcoll" onClick={newCollection}>+ New collection</button>
            <button className="newcoll" onClick={() => setSmartDialog({})}>⚡ New smart collection</button>
            {collections.length === 0 && <p className="side-empty">No collections yet.</p>}
          </nav>
        )}

        <div className="foot">
          <button className="rescan" onClick={() => setSettingsOpen(true)} title="ScreenScraper login & settings">⚙</button>
          <span>{scan ? scan.state : '…'}</span>
          <button className="rescan" onClick={async () => { await api.startScan(false); setScan((s) => ({ ...(s || {}), state: 'running' })) }}
                  disabled={scan && scan.state === 'running'} title="Incremental — only re-reads changed systems">
            {scan && scan.state === 'running' ? 'scanning…' : 'rescan'}
          </button>
          <button className="rescan full" onClick={async () => { await api.startScan(true); setScan((s) => ({ ...(s || {}), state: 'running' })) }}
                  disabled={scan && scan.state === 'running'} title="Full re-scan — re-reads every system (use after a bulk re-scrape)">full</button>
        </div>
      </aside>

      <main className="main">
        {mode === 'missing' ? (
          <MissingView systemId={active} systemName={activeSys ? activeSys.display_name : null} flash={flash} />
        ) : (
        <>
        {mode === 'collections' && collDetail && (
          <CollectionHeader detail={collDetail} onRename={renameCollection} onDelete={deleteCollection} onExport={() => setExportOpen(true)}
                            activeSystem={collSystem} onPickSystem={setCollSystem}
                            onRefresh={refreshCollectionMeta} onPrune={pruneCollectionDangling}
                            onEditRules={() => setSmartDialog({ id: collDetail.id, name: collDetail.name, rules: collDetail.rules })} />
        )}

        <div className="toolbar">
          {mode === 'library' && <h1>{activeSys ? activeSys.display_name : 'ROMForge'}</h1>}
          {mode === 'collections' && <h1 className="sub-h1">{collDetail ? 'Games' : 'Select a collection'}</h1>}
          <span className="count tnum">{total}{(search || coverage) && mode === 'library' ? ` / ${activeSys ? activeSys.rom_count : 0}` : ''} games</span>

          <div className="search">
            <span className="ic">⌕</span>
            <input placeholder="search title…" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          <select className="ctl" value={coverage} onChange={(e) => setCoverage(e.target.value)}>
            <option value="">all coverage</option>
            <option value="complete">complete</option>
            <option value="partial">partial</option>
            <option value="missing">missing</option>
          </select>
          <select className="ctl" value={region} onChange={(e) => setRegion(e.target.value)} title="Filter by region">
            <option value="">all regions</option>
            {REGIONS.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          {genreList.length > 0 && (
            <select className="ctl" value={genre} onChange={(e) => setGenre(e.target.value)} title="Filter by genre">
              <option value="">all genres</option>
              {genreList.map((gn) => <option key={gn} value={gn}>{gn}</option>)}
            </select>
          )}
          <button className={`btn sm ghost fav-filter${onlyFav ? ' on' : ''}`}
                  onClick={() => setOnlyFav((v) => !v)}
                  title={onlyFav ? 'Showing favorites only' : 'Show favorites only'}>♥</button>
          <select className="ctl" value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="name">A–Z</option>
            <option value="name_desc">Z–A</option>
            <option value="favorite">favorites first</option>
            <option value="myrating">my rating</option>
            <option value="rating">top rated</option>
            <option value="year">newest</option>
            <option value="year_asc">oldest</option>
            <option value="coverage">least complete</option>
          </select>
          <div className="views">
            {VIEWS.map((v) => <button key={v} className={v === view ? 'on' : ''} onClick={() => setView(v)}>{v}</button>)}
          </div>

          {total > 0 && (
            <button className={`btn sm${selectMode ? ' sel-on' : ''}`}
                    onClick={() => (selectMode ? exitSelect() : setSelectMode(true))}
                    title="Select games to delete">
              {selectMode ? 'Done' : '☑ Select'}
            </button>
          )}
          {!selectMode && mode === 'library' && activeSys && canScrape && (
            <button className="btn sm ghost" onClick={scrapeSystem} disabled={scrapeBusy}
                    title="Scrape metadata & art for this whole system from ScreenScraper">⬇ scrape</button>
          )}
          {!selectMode && mode === 'library' && activeSys && (
            <button className="btn sm ghost map-btn" onClick={() => setMapOpen(true)} title="Edit this system's frontend mapping">⚙ mapping</button>
          )}
          {!selectMode && mode === 'library' && (
            <CuratePicker collections={collections} curateInto={curateInto} setCurateInto={setCurateInto} onNew={newCollection} />
          )}
          {!selectMode && mode === 'library' && curateInto != null && total > 0 && (
            <button className="btn add-all" onClick={bulkAddFiltered}>+ Add all {total}</button>
          )}
        </div>

        {selectMode && (
          <div className="bulkbar">
            <span className="bulkbar-n tnum">{selectedIds.size} selected</span>
            <span className="muted" style={{ fontSize: 13 }}>click games to pick</span>
            <span style={{ flex: 1 }} />
            <select className="ctl" value="" disabled={!selectedIds.size}
                    onChange={(e) => {
                      const v = e.target.value
                      if (v === '__new') bulkAddToNewCollection()
                      else if (v) bulkAddToCollection(Number(v))
                      e.target.value = ''
                    }}
                    title="Add the selected games to a collection">
              <option value="">+ Add to collection…</option>
              {collections.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              <option value="__new">＋ New collection…</option>
            </select>
            {mode === 'library' && canScrape && (
              <button className="btn sm ghost" onClick={scrapeSelected}
                      disabled={!selectedIds.size || scrapeBusy}
                      title="Re-scrape the selected games from ScreenScraper">⬇ Rescrape</button>
            )}
            <button className="btn sm ghost" onClick={() => setSelectedIds(new Set(games.map((g) => g.id)))}
                    disabled={!games.length}
                    title={hasMore ? `Selects the ${games.length} loaded — load more to reach all ${total}` : 'Select every game'}>
              Select all{hasMore ? ` (${games.length})` : ''}
            </button>
            <button className="btn sm ghost" onClick={() => setSelectedIds(new Set())} disabled={!selectedIds.size}>Clear</button>
            <button className="btn sm danger solid" onClick={bulkDeleteSelected} disabled={!selectedIds.size}>Delete from disk…</button>
          </div>
        )}

        <div className="content">
          {games.length === 0 && !loading ? (
            <div className="empty">
              {mode === 'collections' && !activeCollection ? 'Pick a collection on the left, or create one.'
                : mode === 'collections' ? 'This collection is empty. Switch to Systems and curate games into it.'
                : 'No games match.'}
            </div>
          ) : (
            <>
              <View games={games} onOpen={openGame} sel={sel} onFavorite={toggleFavorite} />
              {hasMore && (
                <div className="more" ref={sentinelRef}>
                  <button onClick={() => setOffset(offset + PAGE)} disabled={loading}>
                    {loading ? `loading… (${games.length} / ${total})` : `load more (${games.length} / ${total})`}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
        </>
        )}
      </main>

      {exportOpen && collDetail && (
        <ExportDialog collectionId={activeCollection} detail={collDetail} onClose={() => setExportOpen(false)} />
      )}
      {mapOpen && active && (
        <MappingEditor systemId={active} onClose={() => setMapOpen(false)} onSaved={loadSystems} />
      )}
      {openId != null && (
        <GameDrawer
          gameId={openId}
          onClose={() => setOpenId(null)}
          canScrape={!!canScrape}
          scrapeBusy={!!scrapeBusy}
          onRescrape={(g) => runScrape({ system_id: g.system_id, game_ids: [g.id] }, g.name)}
          onUpdated={(g) => {
            setGames((gs) => gs.map((x) => (x.id === g.id
              ? { ...x, name: g.name, coverage: g.coverage, developer: g.developer,
                  publisher: g.publisher, genre: g.genre, players: g.players,
                  releasedate: g.releasedate, region: g.region, has: g.has, user: g.user }
              : x)))
            loadSystems()
          }}
          onDeleted={(id, res) => {
            setGames((gs) => gs.filter((g) => g.id !== id))
            setTotal((t) => Math.max(0, t - 1))
            loadSystems()
            if (mode === 'collections' && activeCollection) {
              api.collection(activeCollection).then(setCollDetail).catch(() => {})
            }
            flash(`Deleted “${res?.name || 'game'}” from disk`)
          }}
        />
      )}
      {smartDialog && (
        <SmartCollectionDialog systems={systems} initial={smartDialog.id ? smartDialog : null}
                               onClose={() => setSmartDialog(null)} onSave={saveSmart} />
      )}
      {settingsOpen && (
        <SettingsDialog onClose={() => setSettingsOpen(false)}
                        onSaved={() => { if (active) api.scrapeAvailable(active).then(setScrapeInfo).catch(() => {}) }} />
      )}
      {scrapeModal && (
        <ScrapeStatus status={scrapeStatus} onClose={() => setScrapeModal(false)}
                      onRestart={(req) => restartScrape(req)} />
      )}
      {toast && <div className="banner">{toast}</div>}
      {scrapeBusy && (
        <button className="banner banner-btn" onClick={() => setScrapeModal(true)}>
          ⬇ {scrapeStatus.message || 'Scraping…'}{scrapeStatus.state === 'paused' ? ' (paused)' : ''} — details
        </button>
      )}
      {scan && scan.state === 'running' && <div className="banner">Scanning library…</div>}
    </div>
  )
}
