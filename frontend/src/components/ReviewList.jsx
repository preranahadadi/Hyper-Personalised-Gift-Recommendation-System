import ReviewCard from './ReviewCard'

export default function ReviewList({ runs, onReview }) {
  const items = Object.values(runs)

  if (!items.length) {
    return (
      <div className="empty-state">
        <span className="empty-icon">📭</span>
        <p>No recommendations yet. Run the workflow first.</p>
      </div>
    )
  }

  return (
    <div className="review-list">
      {items.map(run => (
        <ReviewCard key={run.thread_id} run={run} onReview={onReview} />
      ))}
    </div>
  )
}
