import { useState } from 'react'
import { api } from '../api'

const DEMO = [
  ['alice@opsdesk.dev', 'Lead of Engineering'],
  ['bob@opsdesk.dev', 'Member of Engineering'],
  ['carol@opsdesk.dev', 'Member of Engineering'],
  ['ivan@opsdesk.dev', 'Viewer (read only)'],
  ['dave@opsdesk.dev', 'Lead of Support'],
]

export default function Login({ onLogin }) {
  const [email, setEmail] = useState('alice@opsdesk.dev')
  const [password, setPassword] = useState('password123')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setBusy(true); setError(null)
    try {
      const { token } = await api('/auth/login', { method: 'POST', body: { email, password } })
      onLogin(token)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login">
      <form onSubmit={submit} className="card">
        <h1>OpsDesk</h1>
        <p className="muted">Coordinate operational work across teams.</p>
        <label>Email<input value={email} onChange={(e) => setEmail(e.target.value)} autoFocus /></label>
        <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        {error && <div className="error">{error}</div>}
        <button className="primary" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
        <div className="demo-users">
          <div className="muted small">Demo users (password: password123)</div>
          {DEMO.map(([e, role]) => (
            <button type="button" key={e} className="link small" onClick={() => setEmail(e)}>
              {e} <span className="muted">· {role}</span>
            </button>
          ))}
        </div>
      </form>
    </div>
  )
}
