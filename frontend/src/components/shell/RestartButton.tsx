import { useState } from 'react'

// starts the server again with the same command; the page reloads as soon as the new server answers
export default function RestartButton({ className }: { className?: string }) {
  const [busy, setBusy] = useState(false)
  const restart = async () => {
    if (!window.confirm('Restart the server? Work that runs inside the app stops.')) return
    setBusy(true)
    await fetch('/api/restart', { method: 'POST' }).catch(() => null)
    for (let tries = 0; tries < 60; tries++) {
      await new Promise(done => setTimeout(done, 1000))
      const reply = await fetch('/', { cache: 'no-store' }).catch(() => null)
      if (reply?.ok) {
        window.location.reload()
        return
      }
    }
    setBusy(false)
  }
  return <button type="button" className={className} disabled={busy} onClick={restart}>{busy ? 'Restarting…' : 'Restart server'}</button>
}
