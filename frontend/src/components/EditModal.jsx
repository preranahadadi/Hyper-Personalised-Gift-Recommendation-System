import { useState } from 'react'

export default function EditModal({ gifts, onClose, onSave }) {
  const [json, setJson] = useState(JSON.stringify(gifts, null, 2))
  const [error, setError] = useState(null)

  const handleSave = () => {
    try {
      const parsed = JSON.parse(json)
      setError(null)
      onSave(parsed)
    } catch {
      setError('Invalid JSON — fix the syntax before saving.')
    }
  }

  return (
    <div className="modal-backdrop" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal-box">
        <div className="modal-header">
          <h3>Edit Gift Recommendations</h3>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">
          <p className="text-muted">Edit the JSON array of gift objects. Preserve the structure.</p>
          {error && <p className="error-text">{error}</p>}
          <textarea
            className="json-textarea json-textarea-tall"
            value={json}
            onChange={e => setJson(e.target.value)}
            spellCheck={false}
          />
        </div>
        <div className="modal-footer">
          <button className="btn btn-outline" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={handleSave}>✓ Save &amp; Submit</button>
        </div>
      </div>
    </div>
  )
}
