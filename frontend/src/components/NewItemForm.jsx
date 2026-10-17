import { useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api, newIdempotencyKey, PRIORITIES, TYPE_LABEL } from '../api'
import { canWrite } from '../hooks'

export default function NewItemForm({ me, onClose }) {
  const teams = me.teams.filter((t) => canWrite(me, t.id))
  const [form, setForm] = useState({
    team_id: teams[0]?.id, title: '', description: '', type: 'incident', priority: 'medium', due_date: '',
  })
  // One key per form. If the user double-clicks, or the request times out and
  // they press "Create" again, the server sees the SAME key and returns the
  // first result instead of creating a duplicate.
  const idempotencyKey = useRef(newIdempotencyKey())
  const qc = useQueryClient()
  const navigate = useNavigate()

  const create = useMutation({
    mutationFn: () => api('/items', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey.current },
      body: { ...form, team_id: Number(form.team_id), due_date: form.due_date || null },
    }),
    onSuccess: (item) => {
      qc.invalidateQueries({ queryKey: ['items'] })
      qc.invalidateQueries({ queryKey: ['dashboard'] })
      onClose()
      navigate(`/items/${item.id}`)
    },
  })

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }))

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="card modal" onClick={(e) => e.stopPropagation()}
            onSubmit={(e) => { e.preventDefault(); create.mutate() }}>
        <h3>New work item</h3>
        <label>Team
          <select value={form.team_id} onChange={set('team_id')}>
            {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </label>
        <label>Title<input value={form.title} onChange={set('title')} required maxLength={200} autoFocus /></label>
        <label>What happened / why does this exist?
          <textarea rows={4} value={form.description} onChange={set('description')} />
        </label>
        <div className="row">
          <label>Type
            <select value={form.type} onChange={set('type')}>
              {Object.entries(TYPE_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </label>
          <label>Priority
            <select value={form.priority} onChange={set('priority')}>
              {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label>Due date<input type="date" value={form.due_date} onChange={set('due_date')} /></label>
        </div>
        {create.isError && <div className="error">{create.error.message}</div>}
        <div className="row end">
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary" disabled={create.isPending}>{create.isPending ? 'Creating…' : 'Create'}</button>
        </div>
      </form>
    </div>
  )
}
