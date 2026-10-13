import { useEffect, useState } from 'react'

const TRY_SAYING = [
  'Where is my order ORD-101?',
  'Can I cancel ORD-101?',
  'Cancel ORD-103 please.',
  'I want to return ORD-102.',
  'I bought this 20 days ago and opened it. Can I return it?',
  "What's the status of ORD-555?",
  'Is cash on delivery available for a 3,000 rupee order?',
  'Can you book me a flight to Goa?',
]

export default function OrdersPanel() {
  const [orders, setOrders] = useState(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    fetch('/api/orders')
      .then((r) => r.json())
      .then(setOrders)
      .catch(() => setFailed(true))
  }, [])

  return (
    <section className="card">
      <h2>Test orders</h2>
      <p className="muted small">Use these order IDs to try lookups. Each call starts with fresh data.</p>
      {failed && <div className="error">Could not load orders from the backend.</div>}
      {orders?.map((o) => (
        <div className="order" key={o.order_id}>
          <div className="order-top">
            <strong>{o.order_id}</strong>
            <span className={`pill pill-${o.status.toLowerCase().replace(/\s+/g, '-')}`}>{o.status}</span>
          </div>
          <div>{o.product}</div>
          <div className="muted small">
            {o.customer} &middot; &#8377;{o.value_inr}
          </div>
          <div className="muted small">{o.notes}</div>
        </div>
      ))}

      <h3>Try saying</h3>
      <ul className="tryList">
        {TRY_SAYING.map((t) => (
          <li key={t}>&ldquo;{t}&rdquo;</li>
        ))}
      </ul>
    </section>
  )
}
