import { useState, useCallback, useEffect, useRef } from 'react'
import { api } from './api/client'
import RunWorkflow from './components/RunWorkflow'
import ReviewList from './components/ReviewList'
import HistoryTable from './components/HistoryTable'
import TracePanel from './components/TracePanel'
import LoadingOverlay from './components/LoadingOverlay'
import Toast from './components/Toast'

export default function App() {
  const [activeTab, setActiveTab] = useState('run')
  const [runs, setRuns] = useState({})        // threadId → run object
  const [loading, setLoading] = useState(null) // null | { message, detail }
  const [toast, setToast] = useState(null)     // null | { message, type }

  const showToast = useCallback((message, type = 'success') => {
    setToast({ message, type })
    setTimeout(() => setToast(null), 4000)
  }, [])

  const pendingCount = Object.values(runs).filter(r => r.status === 'pending_review').length
  const runningCount = Object.values(runs).filter(r => r.status === 'queued' || String(r.status).startsWith('running')).length

  // Keep a ref so the polling interval always sees the latest runs
  // without needing to be recreated every render.
  const runsRef = useRef(runs)
  useEffect(() => { runsRef.current = runs }, [runs])

  // Poll every 3 s for any run that is still queued or actively running.
  useEffect(() => {
    const interval = setInterval(async () => {
      const active = Object.entries(runsRef.current).filter(
        ([, r]) => r.status === 'queued' || String(r.status).startsWith('running')
      )
      if (!active.length) return

      await Promise.all(
        active.map(async ([threadId]) => {
          try {
            const { data } = await api.getWorkflow(threadId)
            const status = data.human_review?.status || data.workflow_status || data.status
            setRuns(prev => ({
              ...prev,
              [threadId]: { ...prev[threadId], status, result: data },
            }))
            if (status === 'pending_review') {
              showToast(`${data.contact_name} is ready for review`)
            } else if (status === 'error') {
              showToast(`Error for ${data.contact_name}: ${data.error || data.error_message}`, 'error')
            }
          } catch {
            // network hiccup — try again next tick
          }
        })
      )
    }, 3000)

    return () => clearInterval(interval)
  }, [showToast]) // only created once on mount

  // Called by RunWorkflow after API returns
  const onRunComplete = useCallback((results) => {
    setRuns(prev => {
      const next = { ...prev }
      for (const r of results) {
        next[r.thread_id] = r
        if (r.status === 'error') showToast(`Error for ${r.contact_name}: ${r.error}`, 'error')
      }
      return next
    })
    setActiveTab('review')
    const ok = results.filter(r => r.status === 'pending_review').length
    if (ok) showToast(`${ok} recommendation${ok > 1 ? 's' : ''} ready for review`)
  }, [showToast])

  // Called by ReviewCard for approve/reject/edit
  const onReview = useCallback(async (threadId, action, { editedGifts, feedback, tone } = {}) => {
    const msgs = { approve: 'Approving…', reject: 'Rejecting…', edit: 'Saving edits…', regenerate: 'Regenerating…' }
    setLoading({ message: msgs[action] || 'Processing…', detail: action === 'regenerate' ? 'This will take ~30–60 seconds' : '' })
    try {
      const { data } = await api.reviewWorkflow(threadId, action, editedGifts, feedback, tone)
      if (action === 'regenerate' && data.new_thread_id) {
        setRuns(prev => {
          const next = { ...prev }
          delete next[threadId]
          next[data.new_thread_id] = {
            thread_id: data.new_thread_id,
            contact_name: prev[threadId]?.contact_name,
            status: 'pending_review',
            result: data.result,
          }
          return next
        })
      } else {
        setRuns(prev => ({
          ...prev,
          [threadId]: { ...prev[threadId], status: action, result: data.result || prev[threadId]?.result },
        }))
      }
      showToast(`Action "${action}" applied`)
    } catch (e) {
      showToast(e.response?.data?.detail || e.message, 'error')
    } finally {
      setLoading(null)
    }
  }, [showToast])

  return (
    <>
      {loading && <LoadingOverlay message={loading.message} detail={loading.detail} />}
      {toast && <Toast message={toast.message} type={toast.type} />}

      {/* Header */}
      <header className="header">
        <div className="container">
          <div className="header-inner">
            <span className="header-icon">🎁</span>
            <div className="header-copy">
              <span className="header-kicker">AI gifting workspace</span>
              <h1>Gift <span>Recommendation</span> Agent</h1>
              <p>Profile-aware gift ideas with product search, guardrails, traces, and human review.</p>
              <div className="header-pills" aria-label="Workflow capabilities">
                <span>Search verified links</span>
                <span>Review before sending</span>
                <span>Trace every step</span>
              </div>
            </div>
          </div>
        </div>
      </header>

      <main className="container">
        {/* Tabs */}
        <nav className="tabs">
          <button className={activeTab === 'run' ? 'tab active' : 'tab'} onClick={() => setActiveTab('run')}>
            ▶ Run Workflow
          </button>
          <button className={activeTab === 'review' ? 'tab active' : 'tab'} onClick={() => setActiveTab('review')}>
            ✓ Review
            {pendingCount > 0 && <span className="badge">{pendingCount}</span>}
          </button>
          <button className={activeTab === 'trace' ? 'tab active' : 'tab'} onClick={() => setActiveTab('trace')}>
            Trace
            {runningCount > 0 && <span className="badge">{runningCount}</span>}
          </button>
          <button className={activeTab === 'history' ? 'tab active' : 'tab'} onClick={() => setActiveTab('history')}>
            ⏱ History
          </button>
        </nav>

        {/* Tab panels */}
        {activeTab === 'run' && (
          <RunWorkflow onRunComplete={onRunComplete} setLoading={setLoading} showToast={showToast} />
        )}
        {activeTab === 'review' && (
          <ReviewList runs={runs} onReview={onReview} />
        )}
        {activeTab === 'trace' && (
          <TracePanel runs={runs} showToast={showToast} />
        )}
        {activeTab === 'history' && (
          <HistoryTable runs={runs} onLoad={(threadId, result) => {
            setRuns(prev => ({ ...prev, [threadId]: { thread_id: threadId, contact_name: result.contact_name, status: result.human_review?.status || 'unknown', result } }))
            setActiveTab('review')
            showToast('Run loaded')
          }} onServerRuns={(serverRuns) => {
            setRuns(prev => {
              const next = { ...prev }
              for (const run of serverRuns) {
                next[run.thread_id] = {
                  ...next[run.thread_id],
                  thread_id: run.thread_id,
                  contact_name: run.contact_name,
                  status: run.status,
                  created_at: run.created_at,
                  steps_completed: run.steps_completed,
                }
              }
              return next
            })
          }} showToast={showToast} />
        )}
      </main>
    </>
  )
}
