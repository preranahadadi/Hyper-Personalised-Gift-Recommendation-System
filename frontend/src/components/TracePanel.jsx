import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'

const terminalStatuses = new Set(['pending_review', 'approved', 'rejected', 'completed', 'error'])

function formatMs(ms = 0) {
  if (!ms) return '0 ms'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

function nodeLabel(node) {
  return {
    extract_signals: 'Profile signals',
    search_products: 'Product search',
    rank_gifts: 'Gift ranking',
    generate_messages: 'Message generation',
    human_review: 'Human review',
    finalize: 'Finalize',
  }[node] || node
}

function outputFor(steps, node) {
  return steps.find(step => step.node === node)?.output || null
}

function EmptyLine({ text }) {
  return <p className="text-muted">{text}</p>
}

export default function TracePanel({ runs, showToast }) {
  const items = useMemo(() => Object.values(runs), [runs])
  const [selectedId, setSelectedId] = useState('')
  const [trace, setTrace] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!selectedId && items.length) setSelectedId(items[0].thread_id)
  }, [items, selectedId])

  const refreshTrace = async (threadId = selectedId, quiet = false) => {
    if (!threadId) return
    setLoading(true)
    try {
      const { data } = await api.getTrace(threadId)
      setTrace(data)
    } catch (e) {
      if (!quiet) showToast(e.response?.data?.detail || e.message, 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!selectedId) return
    refreshTrace(selectedId, true)
    const interval = setInterval(() => {
      const status = trace?.current_status || runs[selectedId]?.status
      if (!terminalStatuses.has(status)) refreshTrace(selectedId, true)
    }, 4000)
    return () => clearInterval(interval)
  }, [selectedId])

  if (!items.length) {
    return (
      <div className="empty-state">
        <span className="empty-icon">?</span>
        <p>No traces yet. Start a workflow first.</p>
      </div>
    )
  }

  const steps = trace?.step_trace || []
  const currentStatus = trace?.current_status || runs[selectedId]?.status || 'unknown'
  const signalsOutput = outputFor(steps, 'extract_signals')
  const searchOutput = outputFor(steps, 'search_products')
  const rankingOutput = outputFor(steps, 'rank_gifts')
  const products = searchOutput?.products_considered || searchOutput?.sample_products || []
  const rankedGifts = rankingOutput?.summary || []

  return (
    <div className="trace-panel">
      <div className="trace-toolbar">
        <div>
          <label className="trace-label" htmlFor="trace-run">Run</label>
          <select
            id="trace-run"
            className="tone-select"
            value={selectedId}
            onChange={e => setSelectedId(e.target.value)}
          >
            {items.map(run => (
              <option key={run.thread_id} value={run.thread_id}>
                {run.contact_name} - {run.thread_id.slice(0, 8)}
              </option>
            ))}
          </select>
        </div>
        <button className="btn btn-outline btn-sm" onClick={() => refreshTrace()} disabled={loading}>
          Refresh trace
        </button>
      </div>

      <div className="trace-summary">
        <div>
          <span className="trace-label">Current status</span>
          <strong>{currentStatus}</strong>
        </div>
        <div>
          <span className="trace-label">Steps completed</span>
          <strong>{trace?.steps_completed ?? 0}</strong>
        </div>
        <div>
          <span className="trace-label">Wall time</span>
          <strong>{formatMs(trace?.total_wall_ms)}</strong>
        </div>
        <div>
          <span className="trace-label">LLM calls</span>
          <strong>{trace?.token_usage?.total_llm_calls ?? 0}</strong>
        </div>
      </div>

      {!!trace?.all_errors?.length && (
        <div className="trace-warning">
          <strong>Warnings</strong>
          <ul>
            {trace.all_errors.map((err, index) => <li key={index}>{err}</li>)}
          </ul>
        </div>
      )}

      <div className="intermediate-grid">
        <section className="intermediate-card">
          <div className="intermediate-header">
            <span>1</span>
            <h3>Extracted profile signals</h3>
          </div>
          {signalsOutput ? (
            <>
              <div className="signals-section">
                <div className="signals-label">Strong signals</div>
                <div className="signals-row">
                  {(signalsOutput.strong_signals || []).map((signal, index) => (
                    <span className="chip chip-strong" key={index}>{signal}</span>
                  ))}
                </div>
              </div>
              <div className="signals-section">
                <div className="signals-label">Weak signals</div>
                <div className="signals-row">
                  {(signalsOutput.weak_signals || []).map((signal, index) => (
                    <span className="chip chip-weak" key={index}>{signal}</span>
                  ))}
                </div>
              </div>
              <div className="signals-section">
                <div className="signals-label">Signals to avoid</div>
                <div className="signals-row">
                  {(signalsOutput.signals_to_avoid || []).map((signal, index) => (
                    <span className="chip chip-avoid" key={index}>{signal}</span>
                  ))}
                </div>
              </div>
            </>
          ) : <EmptyLine text="Waiting for signal extraction to finish." />}
        </section>

        <section className="intermediate-card">
          <div className="intermediate-header">
            <span>2</span>
            <h3>Search queries used</h3>
          </div>
          {searchOutput?.queries_used?.length ? (
            <ol className="intermediate-list">
              {searchOutput.queries_used.map((query, index) => <li key={index}>{query}</li>)}
            </ol>
          ) : <EmptyLine text="Waiting for query generation and product search." />}
        </section>

        <section className="intermediate-card intermediate-wide">
          <div className="intermediate-header">
            <span>3</span>
            <h3>Products considered</h3>
          </div>
          {products.length ? (
            <div className="products-table-wrap">
              <table className="products-table">
                <thead>
                  <tr>
                    <th>Product</th>
                    <th>Store/domain</th>
                    <th>Price</th>
                    <th>Score</th>
                    <th>Link status</th>
                  </tr>
                </thead>
                <tbody>
                  {products.map((product, index) => (
                    <tr key={`${product.url || product.title}-${index}`}>
                      <td>
                        <a href={product.url} target="_blank" rel="noreferrer">{product.title}</a>
                        {product.source_query && <small>Query: {product.source_query}</small>}
                        {product.validation_reason && <small>{product.validation_reason}</small>}
                      </td>
                      <td>{product.domain || product.store || '-'}</td>
                      <td>{product.price || product.estimated_price || 'Check link'}</td>
                      <td>{product.score ?? '-'}</td>
                      <td>
                        <span className={product.valid === false ? 'link-status review' : 'link-status exact'}>
                          {product.valid === false ? 'Needs review' : 'Exact product'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <EmptyLine text="No product candidates shown yet." />}
        </section>

        <section className="intermediate-card intermediate-wide">
          <div className="intermediate-header">
            <span>4</span>
            <h3>Final ranking, assumptions and confidence</h3>
          </div>
          {rankedGifts.length ? (
            <div className="ranking-list">
              {rankedGifts.map((gift, index) => (
                <div className="ranking-item" key={`${gift.rank}-${gift.gift_name}-${index}`}>
                  <div className="ranking-topline">
                    <strong>#{gift.rank} {gift.gift_name}</strong>
                    <span>{Math.round((gift.confidence || 0) * 100)}% confidence</span>
                  </div>
                  <p>{gift.why_this_gift || gift.personalisation_reasoning || 'Ranking explanation pending.'}</p>
                  <div className="ranking-meta">
                    <span>Store: {gift.store || '-'}</span>
                    <span>Price: {gift.price || '-'}</span>
                    <span>Risk: {gift.risk_level || 'medium'}</span>
                  </div>
                  <div className="assumption-row">
                    <strong>Assumptions:</strong>
                    {(gift.assumptions || []).length ? gift.assumptions.join('; ') : 'None stated'}
                  </div>
                </div>
              ))}
            </div>
          ) : <EmptyLine text="Waiting for gift ranking to finish." />}
        </section>
      </div>

      <div className="trace-timeline">
        {steps.map((step, index) => (
          <div className="trace-step" key={`${step.node}-${index}`}>
            <div className="trace-step-head">
              <div>
                <span className="trace-node">{nodeLabel(step.node)}</span>
                <span className="trace-node-id">{step.node}</span>
              </div>
              <div className="trace-metrics">
                <span>{formatMs(step.duration_ms)}</span>
                <span>{step.llm_calls || 0} LLM</span>
                <span>{(step.prompt_tokens || 0) + (step.completion_tokens || 0)} tokens</span>
              </div>
            </div>
            <pre className="trace-json">{JSON.stringify(step.output, null, 2)}</pre>
          </div>
        ))}
        {!steps.length && (
          <div className="card">
            <div className="card-body text-muted">Waiting for the first workflow node to finish.</div>
          </div>
        )}
      </div>
    </div>
  )
}
