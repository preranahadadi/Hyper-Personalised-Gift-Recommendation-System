export default function LoadingOverlay({ message, detail }) {
  return (
    <div className="overlay">
      <div className="overlay-content">
        <div className="spinner" />
        <p className="overlay-message">{message}</p>
        {detail && <p className="overlay-detail">{detail}</p>}
      </div>
    </div>
  )
}
