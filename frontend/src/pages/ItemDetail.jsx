import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api, PRIORITIES, STATUS_LABEL, TYPE_LABEL, timeAgo } from '../api'
import { canWrite, isLead, useClaim } from '../hooks'
import { useToast } from '../components/Toast.jsx'

const LEAD_ONLY = new Set(['awaiting_approval>resolved', 'awaiting_approval>in_progress'])

function transitionLabel(from, to) {
  if (from === 'open' && to === 'in_progress') return 'Start work'
  if (from === 'awaiting_approval' && to === 'in_progress') return 'Reject (send back)'
  if (from === 'resolved' && to === 'in_progress') return 'Reopen'
  if (to === 'awaiting_approval') return 'Submit for approval'
  if (to === 'resolved') return 'Approve'
  if (to === 'closed') return 'Close'
  return STATUS_LABEL[to]
}

export default function ItemDetail({ me }) {
  const id = Number(useParams().id)
  const qc = useQueryClient()
  const toast = useToast()
  const claim = useClaim(me)
  const [editing, setEditing] = useState(null)   // { baseVersion, fields } while the edit form is open
  const [conflict, setConflict] = useState(null) // latest item from a 409 version_conflict

  // Poll so that a user looking at the page sees changes made by others.
  const itemQ = useQuery({ queryKey: ['item', id], queryFn: () => api(`/items/${id}`), refetchInterval: 10_000 })
  const activityQ = useQuery({ queryKey: ['activity', id], queryFn: () => api(`/items/${id}/activity`), refetchInterval: 10_000 })
  const item = itemQ.data
  const lead = item && isLead(me, item.team_id)
  const membersQ = useQuery({
    queryKey: ['members', item?.team_id],
    queryFn: () => api(`/teams/${item.team_id}/members`),
    enabled: !!lead,
  })

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['item', id] })
    qc.invalidateQueries({ queryKey: ['activity', id] })
    qc.invalidateQueries({ queryKey: ['items'] })
    qc.invalidateQueries({ queryKey: ['dashboard'] })
  }

  // Shared error handling for every action on this page.
  const handleError = (err) => {
    if (err.code === 'version_conflict') {
      setConflict(err.current)
      qc.setQueryData(['item', id], err.current) // show the server's latest copy
    } else {
      toast(err.message)
    }
    refresh()
  }

  const action = useMutation({
    mutationFn: ({ path, body }) => api(`/items/${id}${path}`, { method: 'POST', body }),
    onSuccess: (data) => { qc.setQueryData(['item', id], data); setConflict(null); refresh() },
    onError: handleError,
  })

  const save = useMutation({
    mutationFn: () => api(`/items/${id}`, {
      method: 'PATCH',
      body: { version: editing.baseVersion, ...editing.fields, due_date: editing.fields.due_date || null },
    }),
    onSuccess: (data) => { qc.setQueryData(['item', id], data); setEditing(null); setConflict(null); refresh(); toast('Saved', 'success') },
    onError: handleError,
  })

  const [comment, setComment] = useState('')
  const addComment = useMutation({
    mutationFn: () => api(`/items/${id}/comments`, { method: 'POST', body: { body: comment } }),
    onSuccess: () => { setComment(''); refresh() },
    onError: (err) => toast(err.message),
  })

  if (itemQ.isLoading) return <div className="muted">Loading…</div>
  if (itemQ.isError) return <div className="error">{itemQ.error.message} <Link to="/">Back</Link></div>

  const writer = canWrite(me, item.team_id)
  const mine = item.assignee_id === me.id
  const busy = action.isPending || claim.isPending
  const transitions = item.allowed_transitions.filter((to) =>
    LEAD_ONLY.has(`${item.status}>${to}`) ? lead : lead || (writer && mine))

  const startEdit = () => {
    setConflict(null)
    setEditing({
      baseVersion: item.version,
      fields: { title: item.title, description: item.description, type: item.type,
                priority: item.priority, due_date: item.due_date || '' },
    })
  }
  const setField = (k) => (e) => setEditing((ed) => ({ ...ed, fields: { ...ed.fields, [k]: e.target.value } }))

  return (
    <div className="detail">
      <Link to="/" className="muted small">← Back to list</Link>

      {conflict && (
        <div className="banner warn">
          <strong>This item was changed by someone else.</strong> Your action was not applied, and the page now
          shows the latest version (v{conflict.version}).
          {editing && <> <button className="small" onClick={startEdit}>Reload into the form</button></>}
          <button className="link small" onClick={() => setConflict(null)}>Dismiss</button>
        </div>
      )}
      {editing && !conflict && item.version !== editing.baseVersion && (
        <div className="banner warn">
          Someone updated this item while you were editing. Saving will be rejected -
          <button className="link small" onClick={startEdit}>reload the latest version</button>
        </div>
      )}

      <div className="detail-grid">
        <section className="card">
          {editing ? (
            <form onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
              <label>Title<input value={editing.fields.title} onChange={setField('title')} required maxLength={200} /></label>
              <label>Description<textarea rows={6} value={editing.fields.description} onChange={setField('description')} /></label>
              <div className="row">
                <label>Type<select value={editing.fields.type} onChange={setField('type')}>
                  {Object.entries(TYPE_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select></label>
                <label>Priority<select value={editing.fields.priority} onChange={setField('priority')}>
                  {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
                </select></label>
                <label>Due<input type="date" value={editing.fields.due_date} onChange={setField('due_date')} /></label>
              </div>
              <div className="row end">
                <button type="button" onClick={() => setEditing(null)}>Cancel</button>
                <button className="primary" disabled={save.isPending}>Save</button>
              </div>
            </form>
          ) : (
            <>
              <div className="row between">
                <h2>#{item.id} {item.title}</h2>
                {writer && item.status !== 'closed' && <button onClick={startEdit}>Edit</button>}
              </div>
              <p className="description">{item.description || <span className="muted">No description.</span>}</p>
            </>
          )}

          <h3>Activity</h3>
          {writer && (
            <form className="comment-box" onSubmit={(e) => { e.preventDefault(); if (comment.trim()) addComment.mutate() }}>
              <textarea rows={2} placeholder="Add a comment…" value={comment} onChange={(e) => setComment(e.target.value)} />
              <button disabled={addComment.isPending || !comment.trim()}>Comment</button>
            </form>
          )}
          <ul className="timeline">
            {activityQ.data?.events.map((e) => <TimelineEvent key={e.id} e={e} />)}
          </ul>
        </section>

        <aside className="card side">
          <Field label="Status"><span className={`status status-${item.status}`}>{STATUS_LABEL[item.status]}</span></Field>
          <Field label="Priority"><span className={`prio prio-${item.priority}`}>{item.priority}</span></Field>
          <Field label="Type">{TYPE_LABEL[item.type]}</Field>
          <Field label="Team">{item.team_name}</Field>
          <Field label="Assignee">{item.assignee_name ?? <span className="muted">Unassigned</span>}</Field>
          <Field label="Due">{item.due_date ?? <span className="muted">—</span>}</Field>
          <Field label="Created">by {item.created_by_name}, {timeAgo(item.created_at)}</Field>
          <Field label="Version">v{item.version}</Field>

          <div className="actions">
            {writer && !item.assignee_id && item.status !== 'closed' && (
              <button className="primary" disabled={busy} onClick={() => claim.mutate(item)}>Claim</button>
            )}
            {(mine || lead) && item.assignee_id && ['open', 'in_progress'].includes(item.status) && (
              <button disabled={busy} onClick={() => action.mutate({ path: '/release' })}>Release</button>
            )}
            {transitions.map((to) => (
              <button key={to} disabled={busy}
                      onClick={() => action.mutate({ path: '/transition', body: { to_status: to, version: item.version } })}>
                {transitionLabel(item.status, to)}
              </button>
            ))}
            {lead && item.status !== 'closed' && membersQ.data && (
              <label className="small">Assign to
                <select value="" disabled={busy}
                        onChange={(e) => action.mutate({ path: '/assign', body: { assignee_id: Number(e.target.value), version: item.version } })}>
                  <option value="" disabled>Choose…</option>
                  {membersQ.data.filter((m) => m.role !== 'viewer' && m.id !== item.assignee_id)
                    .map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                </select>
              </label>
            )}
            {!writer && <div className="muted small">You have read-only access to this team.</div>}
          </div>
        </aside>
      </div>
    </div>
  )
}

function Field({ label, children }) {
  return <div className="field"><div className="muted small">{label}</div><div>{children}</div></div>
}

function TimelineEvent({ e }) {
  const d = e.data || {}
  let text
  switch (e.kind) {
    case 'created': text = 'created this item'; break
    case 'claimed': text = 'claimed this item'; break
    case 'released': text = 'released this item (back to open)'; break
    case 'assigned': text = `assigned it to ${d.to_assignee_name}`; break
    case 'status_changed': text = `moved it from ${STATUS_LABEL[d.from]} to ${STATUS_LABEL[d.to]}`; break
    case 'updated': text = 'changed ' + Object.entries(d.changes || {})
      .map(([f, [from, to]]) => f === 'description' ? 'the description' : `${f} from "${from ?? '—'}" to "${to ?? '—'}"`)
      .join(', '); break
    case 'commented': text = 'commented'; break
    default: text = e.kind
  }
  return (
    <li className={e.kind === 'commented' ? 'comment' : ''}>
      <div><strong>{e.actor_name}</strong> {text} <span className="muted small">· {timeAgo(e.created_at)}</span></div>
      {e.kind === 'commented' && <div className="comment-body">{d.body}</div>}
    </li>
  )
}
