import { useState } from 'react'
import { api } from '../api/client'

export default function HistoryTable({ runs, onLoad, onServerRuns, showToast }) {
  const [loading, setLoading] = useState(false)

  const loadFromServer = async () => {
    setLoading(true)
    try {
      const { data } = await api.listWorkflows()
      if (!data.length) { showToast('No runs found on server', 'warning'); return }
      onServerRuns(data)
      showToast(`Found ${data.length} run(s) on server`)
    } catch (e) {
      showToast(e.message, 'error')
    } finally {
      setLoading(false)
    }
  }

  const loadRun = async (threadId) => {
    setLoading(true)
    try {
      const { data } = await api.getWorkflow(threadId)
      onLoad(threadId, data)
    } catch (e) {
      showToast(e.response?.data?.detail || e.message, 'error')
    } finally {
      setLoading(false)
    }
  }

  const items = Object.values(runs)

  return (
    <div>
      <div className="history-header">
        <h3>All Workflow Runs</h3>
        <button className="btn btn-outline btn-sm" onClick={loadFromServer} disabled={loading}>
          ↺ Refresh from server
        </button>
      </div>

      {!items.length ? (
        <div className="empty-state">
          <span className="empty-icon">⏱</span>
          <p>No runs loaded yet. Refresh from server to load saved history.</p>
        </div>
      ) : (
        <table className="history-table">
          <thead>
            <tr>
              <th>Contact</th>
              <th>Status</th>
              <th>Gifts</th>
              <th>Thread ID</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {items.map(run => (
              <tr key={run.thread_id}>
                <td><strong>{run.contact_name}</strong></td>
                <td><span className="status-text">{run.status}</span></td>
                <td>{(run.result?.recommended_gifts || []).length}</td>
                <td><code className="thread-id">{run.thread_id.slice(0, 8)}…</code></td>
                <td>
                  <button
                    className="btn btn-outline btn-sm"
                    onClick={() => loadRun(run.thread_id)}
                    disabled={loading}
                  >
                    Load
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
