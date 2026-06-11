import { useState } from 'react'
import GiftCard from './GiftCard'
import SignalBadges from './SignalBadges'
import EditModal from './EditModal'

const STATUS_CONFIG = {
  queued:         { label: 'Queued…',         cls: 'status-regenerating' },
  running:        { label: 'Processing…',     cls: 'status-regenerating' },
  pending_review: { label: 'Pending Review',  cls: 'status-pending' },
  approved:       { label: 'Approved',        cls: 'status-approved' },
  rejected:       { label: 'Rejected',        cls: 'status-rejected' },
  edited:         { label: 'Edited',          cls: 'status-edited' },
  completed:      { label: 'Completed',       cls: 'status-approved' },
  regenerating:   { label: 'Regenerating…',  cls: 'status-regenerating' },
  error:          { label: 'Error',           cls: 'status-rejected' },
}

export default function ReviewCard({ run, onReview }) {
  const [tone, setTone] = useState('professional')
  const [editOpen, setEditOpen] = useState(false)
  const [traceOpen, setTraceOpen] = useState(false)

  if (run.status === 'error') {
    return (
      <div className="card card-error">
        <div className="card-body">
          ⚠ <strong>{run.contact_name}</strong> — {run.error}
        </div>
      </div>
    )
  }

  const r = run.result || {}
  const signals = r.profile_signals || {}
  const gifts = r.recommended_gifts || []
  const trace = r.search_trace || {}
  const rawStatus = r.human_review?.status || run.status || 'queued'
  // Collapse "running:extract_signals" → "running" for the badge lookup
  const reviewStatus = rawStatus.startsWith('running:') ? 'running' : rawStatus
  const { label, cls } = STATUS_CONFIG[reviewStatus] || { label: rawStatus, cls: 'status-regenerating' }
  const isPending = reviewStatus === 'pending_review'
  const isProcessing = reviewStatus === 'queued' || reviewStatus === 'running'

  return (
    <>
      <div className="card review-card">
        {/* Header */}
        <div className="card-header review-header">
          <div className="review-title">
            <strong>{run.contact_name}</strong>
            <span className={`status-badge ${cls}`}>{label}</span>
          </div>
          <div className="review-meta">
            <span>🔍 {trace.products_considered_count || 0} products</span>
            <span>⚙ {(trace.queries_used || []).length} queries</span>
          </div>
        </div>

        <div className="card-body">
          {/* Signals */}
          <SignalBadges signals={signals} />

          {/* Search trace */}
          <details className="trace-details" open={traceOpen} onToggle={e => setTraceOpen(e.target.open)}>
            <summary>Search queries used ({(trace.queries_used || []).length})</summary>
            <ul className="trace-list">
              {(trace.queries_used || []).map((q, i) => <li key={i}>{q}</li>)}
            </ul>
          </details>

          {/* Gift cards */}
          {isProcessing
            ? <p className="text-muted">⏳ Generating recommendations — this takes 2–5 minutes per contact…</p>
            : gifts.length
              ? gifts.map(g => <GiftCard key={g.rank} gift={g} />)
              : <p className="text-muted">No recommendations generated.</p>
          }

          {/* Errors */}
          {(r.errors || []).filter(Boolean).length > 0 && (
            <details className="errors-details">
              <summary>⚠ Workflow warnings ({r.errors.filter(Boolean).length})</summary>
              <ul className="trace-list">{r.errors.filter(Boolean).map((e, i) => <li key={i}>{e}</li>)}</ul>
            </details>
          )}
        </div>

        {/* Action bar — only shown for pending runs */}
        {isPending && (
          <div className="action-bar">
            <div className="action-left">
              <button className="btn btn-approve" onClick={() => onReview(run.thread_id, 'approve', { tone })}>
                ✓ Approve
              </button>
              <button className="btn btn-reject" onClick={() => onReview(run.thread_id, 'reject', { tone })}>
                ✕ Reject
              </button>
              <button className="btn btn-edit" onClick={() => setEditOpen(true)}>
                ✎ Edit
              </button>
            </div>
            <div className="action-right">
              <select className="tone-select" value={tone} onChange={e => setTone(e.target.value)}>
                <option value="professional">Professional tone</option>
                <option value="warm">Warm tone</option>
                <option value="formal">Formal tone</option>
              </select>
              <button
                className="btn btn-regenerate"
                onClick={() => onReview(run.thread_id, 'regenerate', { tone })}
              >
                ↺ Regenerate
              </button>
            </div>
          </div>
        )}
      </div>

      {editOpen && (
        <EditModal
          gifts={gifts}
          onClose={() => setEditOpen(false)}
          onSave={(edited) => {
            setEditOpen(false)
            onReview(run.thread_id, 'edit', { editedGifts: edited, tone })
          }}
        />
      )}
    </>
  )
}
