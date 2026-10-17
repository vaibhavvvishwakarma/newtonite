// Small fetch wrapper. Every server error becomes an ApiError with the
// server's `code`, `message` and (for conflicts) the `current` item.

const TOKEN_KEY = 'opsdesk_token'

export function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) } catch { return null }
}
export function setToken(token) {
  try { token ? localStorage.setItem(TOKEN_KEY, token) : localStorage.removeItem(TOKEN_KEY) } catch { /* ignore */ }
}

export class ApiError extends Error {
  constructor(status, body) {
    const err = body?.error || {}
    super(err.message || `Request failed (${status})`)
    this.status = status
    this.code = err.code || 'unknown'
    this.current = err.current       // latest item, sent with 409 conflicts
    this.details = err.details       // field errors, sent with 422
  }
}

let onUnauthorized = () => {}
export function setUnauthorizedHandler(fn) { onUnauthorized = fn }

export async function api(path, { method = 'GET', body, headers = {} } = {}) {
  let res
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
        ...headers,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, { error: { code: 'network', message: 'Cannot reach the server. Check your connection and try again.' } })
  }
  const data = res.status === 204 ? null : await res.json().catch(() => null)
  if (!res.ok) {
    if (res.status === 401 && path !== '/auth/login') onUnauthorized()
    throw new ApiError(res.status, data)
  }
  return data
}

export function newIdempotencyKey() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

// Display helpers
export const STATUS_LABEL = {
  open: 'Open', in_progress: 'In progress', awaiting_approval: 'Awaiting approval',
  resolved: 'Resolved', closed: 'Closed',
}
export const TYPE_LABEL = {
  incident: 'Incident', customer_issue: 'Customer issue', payment: 'Payment',
  compliance: 'Compliance', task: 'Task',
}
export const PRIORITIES = ['urgent', 'high', 'medium', 'low']

export function timeAgo(iso) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
