import React from 'react'
import { mediaUrl } from '../api.js'
import { CoveragePill, GapFlags, Thumb, Check, RetryImg, formatSize } from './bits.jsx'

// `sel` = { active: bool, isOn: (game)=>bool, toggle: (game)=>void }
// When sel.active, each item shows a selection checkbox and a selected style.

function selProps(sel, g) {
  if (!sel || !sel.active) return { on: false, node: null }
  const on = sel.isOn(g)
  return { on, node: <Check on={on} onToggle={() => sel.toggle(g)} /> }
}

export function ListView({ games, onOpen, sel }) {
  return (
    <div className="lview">
      {games.map((g) => {
        const s = selProps(sel, g)
        const selc = sel && sel.active ? ' selectable' : ''
        return (
          <div key={g.id} className={`lrow${s.on ? ' sel' : ''}${selc}`} onClick={() => onOpen(g)}>
            {s.node}
            <div className="t">
              <b>{g.name}</b>
              <span>{g.rom_filename}</span>
            </div>
            <div className="meta">{g.genre || ''}{g.genre && g.releasedate ? ' · ' : ''}{(g.releasedate || '').slice(0, 4)}</div>
            <CoveragePill value={g.coverage} />
          </div>
        )
      })}
    </div>
  )
}

export function DetailsView({ games, onOpen, sel }) {
  return (
    <div className="dview">
      {games.map((g) => {
        const s = selProps(sel, g)
        const selc = sel && sel.active ? ' selectable' : ''
        return (
          <div key={g.id} className={`dcard${s.on ? ' sel' : ''}${selc}`} onClick={() => onOpen(g)}>
            {s.node}
            <Thumb gameId={g.id} asset="cover" w={120} has={g.has.cover} label="no art" />
            <div className="body">
              <b>{g.name}</b>
              <div className="sub">
                {[g.developer, g.publisher].filter(Boolean).join(' · ') || '—'}
                {g.releasedate ? ` · ${g.releasedate.slice(0, 4)}` : ''}
                {g.players ? ` · ${g.players}P` : ''}
              </div>
              {g.description && <div className="desc">{g.description}</div>}
            </div>
            <CoveragePill value={g.coverage} />
          </div>
        )
      })}
    </div>
  )
}

export function BoxartView({ games, onOpen, sel }) {
  return (
    <div className="grid">
      {games.map((g) => {
        const s = selProps(sel, g)
        return (
          <div key={g.id} className={`cardb${s.on ? ' sel' : ''}`} onClick={() => onOpen(g)}>
            <div className="art">
              {s.node}
              {g.has.cover ? (
                <RetryImg src={mediaUrl(g.id, 'cover', 320)} alt={g.name}
                          fallback={<span className="noart">{g.name}</span>} />
              ) : (
                <span className="noart">{g.name}</span>
              )}
            </div>
            <div className="cap">{g.name}</div>
          </div>
        )
      })}
    </div>
  )
}

// The requested "nice to have": name + boxart + screenshot + what's missing.
export function GapsView({ games, onOpen, sel }) {
  return (
    <div className="gaps">
      {games.map((g, i) => {
        const s = selProps(sel, g)
        return (
          <div key={g.id} className={`grow${s.on ? ' sel' : ''}`} onClick={() => onOpen(g)}>
            {sel && sel.active ? s.node : <span className="idx tnum">{i + 1}</span>}
            <div className="ttl">
              <b>{g.name}</b>
              <span>{g.rom_filename}</span>
            </div>
            <Thumb gameId={g.id} asset="cover" w={120} has={g.has.cover} label="no box" />
            <Thumb gameId={g.id} asset="screenshot" w={200} wide has={g.has.screenshot} label="no shot" />
            <div className="flags"><GapFlags has={g.has} /></div>
          </div>
        )
      })}
    </div>
  )
}

export { formatSize }
