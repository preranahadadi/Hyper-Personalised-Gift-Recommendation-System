import { useState, useRef } from 'react'
import { api } from '../api/client'

export default function RunWorkflow({ onRunComplete, setLoading, showToast }) {
  const [singleJson, setSingleJson] = useState('')
  const [selectedFileName, setSelectedFileName] = useState('')
  const fileRef = useRef(null)

  const handleFileRun = async () => {
    const file = fileRef.current?.files?.[0]
    if (!file) { showToast('Please choose a contact file', 'warning'); return }

    setLoading({ message: 'Preparing recommendations...', detail: '~30-90 seconds per contact' })
    try {
      const { data } = await api.runWorkflowFromFile(file)
      onRunComplete(data)
    } catch (e) {
      showToast(e.response?.data?.detail || e.message, 'error')
    } finally {
      setLoading(null)
    }
  }

  const handleSingleRun = async () => {
    if (!singleJson.trim()) { showToast('Please paste a contact JSON object', 'warning'); return }
    let contact
    try { contact = JSON.parse(singleJson) } catch { showToast('Invalid JSON', 'error'); return }

    setLoading({ message: `Preparing ${contact.name || 'contact'}...`, detail: '~30-90 seconds' })
    try {
      const { data } = await api.runWorkflow([contact])
      onRunComplete(data)
    } catch (e) {
      showToast(e.response?.data?.detail || e.message, 'error')
    } finally {
      setLoading(null)
    }
  }

  return (
    <div className="run-page">
      <section className="run-intro">
        <div className="run-intro-copy">
          <span className="eyebrow">Recommendation workspace</span>
          <h2>Turn profile signals into review-ready gift ideas.</h2>
          <p>
            Start with a contact list or test one profile. The agent extracts signals,
            searches products, ranks options, writes a note, and pauses for human review.
          </p>
        </div>
        <div className="run-steps" aria-label="Workflow steps">
          <div><strong>1</strong><span>Signals</span></div>
          <div><strong>2</strong><span>Products</span></div>
          <div><strong>3</strong><span>Review</span></div>
        </div>
      </section>

      <div className="run-grid">
        <div className="card run-card primary-run-card">
          <div className="card-header">
            <span className="card-icon text-icon">Batch</span>
            Contact list
          </div>
          <div className="card-body">
            <p className="text-muted">
              Select a JSON file with one or more contacts. Best for running the full sample set.
            </p>
            <label className="file-drop">
              <input
                ref={fileRef}
                type="file"
                accept=".json"
                onChange={e => setSelectedFileName(e.target.files?.[0]?.name || '')}
              />
              <span className="file-drop-title">{selectedFileName || 'Choose contact file'}</span>
              <span className="file-drop-subtitle">JSON array or single contact object</span>
            </label>
            <button className="btn btn-primary btn-full" onClick={handleFileRun}>
              Start batch run
            </button>
          </div>
        </div>

        <div className="card run-card">
          <div className="card-header">
            <span className="card-icon text-icon">Single</span>
            One-contact test
          </div>
          <div className="card-body">
            <p className="text-muted">
              Paste one contact object when you want a quick sanity check before a batch run.
            </p>
            <textarea
              className="json-textarea"
              rows={9}
              value={singleJson}
              onChange={e => setSingleJson(e.target.value)}
              placeholder='{"name": "Aarav Mehta", "role": "VP Sales", ...}'
            />
            <button className="btn btn-outline btn-full" onClick={handleSingleRun}>
              Start single run
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
