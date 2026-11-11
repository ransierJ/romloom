// Thin API client. All paths are same-origin: proxied to FastAPI in dev,
// served by the same container in production.

async function get(path) {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function send(path, method, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.status === 204 ? null : res.json()
}

export const api = {
  stats: () => get('/api/stats'),
  systems: () => get('/api/systems'),
  scanStatus: () => get('/api/scan/status'),
  startScan: (force = false) => fetch(`/api/scan${force ? '?force=true' : ''}`, { method: 'POST' }),

  games(systemId, { search = '', coverage = '', region = '', sort = 'name', limit = 100, offset = 0, selectedFor = null } = {}) {
    const p = new URLSearchParams({ sort, limit, offset })
    if (search) p.set('search', search)
    if (coverage) p.set('coverage', coverage)
    if (region) p.set('region', region)
    if (selectedFor != null) p.set('selected_for', selectedFor)
    return get(`/api/systems/${encodeURIComponent(systemId)}/games?${p}`)
  },

  game: (id) => get(`/api/games/${id}`),

  getSettings: () => get('/api/settings'),
  patchSettings: (body) => send('/api/settings', 'PATCH', body),
  scrapeAvailable: (systemId) => get(`/api/scrape/available${systemId ? `?system_id=${encodeURIComponent(systemId)}` : ''}`),
  startScrape: (body) => send('/api/scrape', 'POST', body),
  scrapeStatus: () => get('/api/scrape/status'),
  scrapeStop: () => send('/api/scrape/stop', 'POST'),
  scrapePause: () => send('/api/scrape/pause', 'POST'),
  scrapeResume: () => send('/api/scrape/resume', 'POST'),
  editMetadata: (id, fields) => send(`/api/games/${id}/metadata`, 'PATCH', fields),
  async uploadMedia(id, asset, file) {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch(`/api/games/${id}/media/${asset}`, { method: 'POST', body: fd })
    if (!res.ok) { const e = new Error(`${res.status}`); e.status = res.status; throw e }
    return res.json()
  },

  missing(systemId, { region = 'USA', search = '', limit = 500, offset = 0 } = {}) {
    const p = new URLSearchParams({ region, limit, offset })
    if (search) p.set('search', search)
    return get(`/api/systems/${encodeURIComponent(systemId)}/missing?${p}`)
  },
  refreshCatalog: (systemId) => send(`/api/systems/${encodeURIComponent(systemId)}/catalog/refresh`, 'POST'),

  // Permanent delete from disk — surfaces the server's detail (e.g. read-only).
  async deleteGame(id) {
    const res = await fetch(`/api/games/${id}`, { method: 'DELETE' })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      const err = new Error(body.detail || `${res.status} ${res.statusText}`)
      err.status = res.status
      throw err
    }
    return res.json()
  },

  async bulkDelete(ids) {
    const res = await fetch('/api/games/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      const err = new Error(body.detail || `${res.status} ${res.statusText}`)
      err.status = res.status
      throw err
    }
    return res.json()
  },

  // ---- collections ----
  collections: () => get('/api/collections'),
  collection: (id) => get(`/api/collections/${id}`),
  createCollection: (name, note) => send('/api/collections', 'POST', { name, note }),
  patchCollection: (id, body) => send(`/api/collections/${id}`, 'PATCH', body),
  deleteCollection: (id) => send(`/api/collections/${id}`, 'DELETE'),
  addGames: (id, games) => send(`/api/collections/${id}/add`, 'POST', { games }),
  addGamesByIds: (id, ids) => send(`/api/collections/${id}/add-ids`, 'POST', { ids }),
  refreshCollection: (id) => send(`/api/collections/${id}/refresh`, 'POST'),
  pruneCollection: (id) => send(`/api/collections/${id}/prune`, 'POST'),
  removeGames: (id, games) => send(`/api/collections/${id}/remove`, 'POST', { games }),
  addByQuery: (id, q) => send(`/api/collections/${id}/add-query`, 'POST', q),

  // ---- export ----
  exportCollection: (id, body) => send(`/api/collections/${id}/export`, 'POST', body),
  exportStatus: () => get('/api/export/status'),

  // ---- presets ----
  presets: () => get('/api/presets'),
  createPreset: (body) => send('/api/presets', 'POST', body),
  deletePreset: (id) => send(`/api/presets/${id}`, 'DELETE'),

  // ---- mappings ----
  systemMapping: (sid) => get(`/api/systems/${encodeURIComponent(sid)}/mapping`),
  patchSystemMapping: (sid, body) => send(`/api/systems/${encodeURIComponent(sid)}/mapping`, 'PATCH', body),

  collectionGames(id, { search = '', coverage = '', region = '', sort = 'name', system = '', limit = 100, offset = 0 } = {}) {
    const p = new URLSearchParams({ sort, limit, offset })
    if (search) p.set('search', search)
    if (coverage) p.set('coverage', coverage)
    if (region) p.set('region', region)
    if (system) p.set('system', system)
    return get(`/api/collections/${id}/games?${p}`)
  },
}

// A game's stable natural key, for add/remove payloads.
export const gameRef = (g) => ({ system_id: g.system_id, rom_filename: g.rom_filename })

// Build a media URL; w=0 (default) serves full-size, otherwise a cached thumbnail.
export function mediaUrl(gameId, asset, w = 0) {
  const q = w ? `?w=${w}` : ''
  return `/api/games/${gameId}/media/${asset}${q}`
}

// Gameplay video stream (supports range requests for seeking).
export function videoUrl(gameId) {
  return `/api/games/${gameId}/video`
}
