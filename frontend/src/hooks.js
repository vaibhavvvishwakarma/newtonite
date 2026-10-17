import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import { useToast } from './components/Toast.jsx'

// Role helpers - only used to HIDE buttons. The server enforces the real rules.
export function roleIn(me, teamId) {
  return me.teams.find((t) => t.id === teamId)?.role
}
export function canWrite(me, teamId) {
  return ['member', 'lead'].includes(roleIn(me, teamId))
}
export function isLead(me, teamId) {
  return roleIn(me, teamId) === 'lead'
}

/**
 * Claim with an OPTIMISTIC update:
 *   1. onMutate  - immediately show "me" as the assignee everywhere (list + detail),
 *                  keeping a snapshot of the old data.
 *   2. onError   - the server said no (e.g. 409 already claimed by someone else):
 *                  put the snapshot back (roll back) and tell the user why.
 *   3. onSettled - always refetch, so the screen ends up showing the server's truth.
 */
export function useClaim(me) {
  const qc = useQueryClient()
  const toast = useToast()

  return useMutation({
    mutationFn: (item) => api(`/items/${item.id}/claim`, { method: 'POST' }),

    onMutate: async (item) => {
      await qc.cancelQueries({ queryKey: ['items'] })
      await qc.cancelQueries({ queryKey: ['item', item.id] })
      const snapshot = {
        lists: qc.getQueriesData({ queryKey: ['items'] }),
        detail: qc.getQueryData(['item', item.id]),
      }
      const patch = (it) => (it.id === item.id ? { ...it, assignee_id: me.id, assignee_name: me.name } : it)
      qc.setQueriesData({ queryKey: ['items'] }, (data) =>
        data && { ...data, pages: data.pages.map((p) => ({ ...p, items: p.items.map(patch) })) })
      qc.setQueryData(['item', item.id], (d) => d && patch(d))
      return snapshot
    },

    onError: (err, item, snapshot) => {
      snapshot.lists.forEach(([key, data]) => qc.setQueryData(key, data))
      qc.setQueryData(['item', item.id], snapshot.detail)
      toast(err.code === 'already_claimed' ? `Could not claim #${item.id}: ${err.message}` : err.message)
    },

    onSuccess: (item) => toast(`You now own #${item.id}`, 'success'),

    onSettled: (_data, _err, item) => {
      qc.invalidateQueries({ queryKey: ['items'] })
      qc.invalidateQueries({ queryKey: ['item', item.id] })
      qc.invalidateQueries({ queryKey: ['activity', item.id] })
      qc.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}
