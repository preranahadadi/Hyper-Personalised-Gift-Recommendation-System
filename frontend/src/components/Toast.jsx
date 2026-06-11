export default function Toast({ message, type = 'success' }) {
  return (
    <div className={`toast toast-${type}`}>
      {type === 'success' && '✓ '}
      {type === 'error'   && '✕ '}
      {type === 'warning' && '⚠ '}
      {message}
    </div>
  )
}
