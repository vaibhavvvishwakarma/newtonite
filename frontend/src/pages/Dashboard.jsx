import { useEffect, useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, PRIORITIES, STATUS_LABEL, TYPE_LABEL, timeAgo } from '../api'
import { canWrite, useClaim } from '../hooks'
import NewItemForm from '../components/NewItemForm.jsx'

const VIEWS = [
  { key: 'mine', label: 'Assigned to me' },
  { key: 'unassigned_urgent', label: 'Unassigned urgent' },
  { key: 'awaiting_my_approval', label: 'Awaiting my approval' },
  { key: 'all', label: 'All items' },
]

function useDebounced(value, ms = 300) {
  const [v, setV] = useState(value)
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t) }, [value, ms])
  return v
}

export default function Dashboard({ me }) {
  const [view, setView] = useState('mine')
  const [filters, setFilters] = useState({ team_id: '', status: '', priority: '', type: '' })
  const [search, setSearch] = useState('')
  const [showNew, setShowNew] = useState(false)
  const q = useDebounced(search)
  const claim = useClaim(me)

  const counts = useQuery({ queryKey: ['dashboard'], queryFn: () => api('/dashboard'), refetchInterval: 30_000 })

  // All filtering happens on the SERVER. The browser only ever holds the pages it has loaded.
  const params = { view, ...filters, q }
  const list = useInfiniteQuery({
    queryKey: ['items', params],
    queryFn: ({ pageParam }) => {
      const qs = new URLSearchParams(Object.entries({ ...params, cursor: pageParam || '' }).filter(([, v]) => v))
      return api(`/items?${qs}`)
    },
    initialPageParam: null,
    getNextPageParam: (last) => last.next_cursor,
    refetchInterval: 30_000,
  })
  const items = list.data?.pages.flatMap((p) => p.items) ?? []
  const setFilter = (k) => (e) => setFilters((f) => ({ ...f, [k]: e.target.value }))

  return (
    <div>
      <div className="row between">
        <h2>Work items</h2>
        {me.teams.some((t) => canWrite(me, t.id)) && (
          <button className="primary" onClick={() => setShowNew(true)}>+ New item</button>
        )}
      </div>

      <div className="tabs">
        {VIEWS.map((v) => (
          <button key={v.key} className={`tab ${view === v.key ? 'active' : ''}`} onClick={() => setView(v.key)}>
            {v.label}
            {counts.data && v.key in counts.data && <span className="count">{counts.data[v.key]}</span>}
          </button>
        ))}
      </div>

      <div className="filters">
        <input placeholder="Search title or description…" value={search} onChange={(e) => setSearch(e.target.value)} />
        <select value={filters.team_id} onChange={setFilter('team_id')}>
          <option value="">All my teams</option>
          {me.teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        <select value={filters.status} onChange={setFilter('status')}>
          <option value="">Any status</option>
          {Object.entries(STATUS_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <select value={filters.priority} onChange={setFilter('priority')}>
          <option value="">Any priority</option>
          {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <select value={filters.type} onChange={setFilter('type')}>
          <option value="">Any type</option>
          {Object.entries(TYPE_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </div>

      {list.isError && <div className="error">{list.error.message}</div>}

      <table className="items">
        <thead>
          <tr><th>#</th><th>Title</th><th>Team</th><th>Type</th><th>Priority</th><th>Status</th><th>Assignee</th><th>Updated</th></tr>
        </thead>
        <tbody>
          {items.map((it) => (
            <tr key={it.id}>
              <td className="muted">{it.id}</td>
              <td><Link to={`/items/${it.id}`}>{it.title}</Link></td>
              <td>{it.team_name}</td>
              <td>{TYPE_LABEL[it.type]}</td>
              <td><span className={`prio prio-${it.priority}`}>{it.priority}</span></td>
              <td><span className={`status status-${it.status}`}>{STATUS_LABEL[it.status]}</span></td>
              <td>
                {it.assignee_name ?? (
                  canWrite(me, it.team_id) && it.status !== 'closed'
                    ? <button className="small" disabled={claim.isPending} onClick={() => claim.mutate(it)}>Claim</button>
                    : <span className="muted">—</span>
                )}
              </td>
              <td className="muted">{timeAgo(it.updated_at)}</td>
            </tr>
          ))}
          {!list.isLoading && items.length === 0 && (
            <tr><td colSpan={8} className="muted center">Nothing here.</td></tr>
          )}
        </tbody>
      </table>

      <div className="center">
        {list.isLoading && <span className="muted">Loading…</span>}
        {list.hasNextPage && (
          <button onClick={() => list.fetchNextPage()} disabled={list.isFetchingNextPage}>
            {list.isFetchingNextPage ? 'Loading…' : 'Load more'}
          </button>
        )}
      </div>

      {showNew && <NewItemForm me={me} onClose={() => setShowNew(false)} />}
    </div>
  )
}
