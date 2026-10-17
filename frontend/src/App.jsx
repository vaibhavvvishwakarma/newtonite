import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, Route, Routes } from 'react-router-dom'
import { api, getToken, setToken, setUnauthorizedHandler } from './api'
import Login from './pages/Login.jsx'
import Dashboard from './pages/Dashboard.jsx'
import ItemDetail from './pages/ItemDetail.jsx'
import Notifications from './components/Notifications.jsx'

export default function App() {
  const [token, setTok] = useState(getToken())
  const qc = useQueryClient()

  const logout = () => { setToken(null); setTok(null); qc.clear() }
  useEffect(() => setUnauthorizedHandler(logout), [])

  const me = useQuery({ queryKey: ['me'], queryFn: () => api('/me'), enabled: !!token })

  if (!token) return <Login onLogin={(t) => { setToken(t); setTok(t) }} />
  if (me.isLoading) return <div className="center muted">Loading…</div>
  if (me.isError) return <div className="center">Could not load your profile. <button onClick={logout}>Log in again</button></div>

  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">OpsDesk</Link>
        <div className="topbar-right">
          <Notifications />
          <span className="muted">
            {me.data.name} · {me.data.teams.map((t) => `${t.name} (${t.role})`).join(', ')}
          </span>
          <button className="link" onClick={logout}>Log out</button>
        </div>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<Dashboard me={me.data} />} />
          <Route path="/items/:id" element={<ItemDetail me={me.data} />} />
        </Routes>
      </main>
    </div>
  )
}
