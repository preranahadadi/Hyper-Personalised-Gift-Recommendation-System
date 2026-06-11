const RISK_CONFIG = {
  low: { label: 'Low risk', cls: 'risk-low' },
  medium: { label: 'Medium risk', cls: 'risk-medium' },
  high: { label: 'High risk', cls: 'risk-high' },
}

function ConfidenceBar({ score = 0 }) {
  const pct = Math.round(score * 100)
  const color = score >= 0.8 ? '#10b981' : score >= 0.6 ? '#f59e0b' : '#ef4444'
  return (
    <div className="confidence-row">
      <div className="confidence-track">
        <div className="confidence-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="confidence-label">{pct}% confidence</span>
    </div>
  )
}

export default function GiftCard({ gift }) {
  const { label: riskLabel, cls: riskCls } = RISK_CONFIG[gift.risk_level] || RISK_CONFIG.medium

  return (
    <div className="gift-card">
      <div className="gift-header">
        <span className="gift-rank">#{gift.rank}</span>
        <div className="gift-title-block">
          <span className="gift-name">{gift.gift_name}</span>
          <div className="gift-badges">
            <span className={`risk-badge ${riskCls}`}>{riskLabel}</span>
            {gift.estimated_price && (
              <span className="price-badge">{gift.estimated_price}</span>
            )}
          </div>
        </div>
      </div>

      {gift.product_url ? (
        <a href={gift.product_url} target="_blank" rel="noopener noreferrer" className="gift-link">
          Open product: {gift.store || gift.product_url}
        </a>
      ) : (
        <span className="gift-link gift-link-missing">
          Product link unavailable - review needed
        </span>
      )}

      <ConfidenceBar score={gift.confidence_score} />

      <p className="gift-why"><strong>Why:</strong> {gift.why_this_gift}</p>
      <p className="gift-why"><strong>Signal match:</strong> {gift.personalisation_reasoning}</p>

      <div className="gift-message">
        <span className="message-icon">Note</span>
        <em>{gift.personalised_message}</em>
      </div>

      {gift.assumptions?.length > 0 && (
        <p className="gift-assumptions">
          Info: {gift.assumptions.join(' | ')}
        </p>
      )}
    </div>
  )
}
