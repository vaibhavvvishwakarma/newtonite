import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, timeAgo } from '../api'

// Notifications are created by the background worker, so we poll for them.
export default function Notifications() {
  const [open, setOpen] = useState(false)
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['notifications'],
    queryFn: () => api('/notifications'),
    refetchInterval: 15_000,
  })
  const readAll = useMutation({
    mutationFn: () => api('/notifications/read-all', { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notifications'] }),
  })

  return (
    <div className="notif">
      <button className="ghost" onClick={() => setOpen(!open)}>
        Notifications{data?.unread ? <span className="pill">{data.unread}</span> : null}
      </button>
      {open && (
        <div className="notif-panel card">
          <div className="row between">
            <strong>Notifications</strong>
            {data?.unread > 0 && <button className="link small" onClick={() => readAll.mutate()}>Mark all read</button>}
          </div>
          {!data?.notifications.length && <div className="muted small">Nothing yet.</div>}
          {data?.notifications.map((n) => (
            <Link key={n.id} to={`/items/${n.item_id}`} onClick={() => setOpen(false)}
                  className={`notif-item ${n.read_at ? '' : 'unread'}`}>
              <div>{n.message}</div>
              <div className="muted small">{timeAgo(n.created_at)}</div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
