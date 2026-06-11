export default function SignalBadges({ signals }) {
  const strong = signals.strong_signals || []
  const weak   = signals.weak_signals   || []
  const avoid  = signals.signals_to_avoid || []

  if (!strong.length && !weak.length) return null

  return (
    <div className="signals-section">
      <p className="signals-label">Profile Signals</p>
      <div className="signals-row">
        {strong.map((s, i) => <span key={i} className="chip chip-strong">{s}</span>)}
        {weak.map((s, i)   => <span key={i} className="chip chip-weak">{s}</span>)}
      </div>
      {avoid.length > 0 && (
        <div className="signals-row">
          {avoid.map((s, i) => <span key={i} className="chip chip-avoid">{s}</span>)}
        </div>
      )}
    </div>
  )
}
