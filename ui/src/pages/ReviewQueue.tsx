const SAMPLE = [
  {
    id: "cls-e7f3a2b1",
    text: "App crashes every time I try to checkout on Android",
    bu: "Mobile Engineering",
    category: "Crash",
    confidence: 0.62,
    reason: "Below threshold",
  },
  {
    id: "cls-a1b2c3d4",
    text: "Payment failed but amount was deducted",
    bu: "Payments",
    category: "Billing",
    confidence: 0.71,
    reason: "Below threshold",
  },
];

export default function ReviewQueue() {
  return (
    <>
      <h2 className="page-title">Review queue</h2>
      <p className="page-sub">
        Low-confidence classifications awaiting human approval before ticket
        generation
      </p>
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Feedback</th>
            <th>Suggested BU</th>
            <th>Category</th>
            <th>Confidence</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {SAMPLE.map((r) => (
            <tr key={r.id}>
              <td>
                <code>{r.id}</code>
              </td>
              <td>{r.text}</td>
              <td>{r.bu}</td>
              <td>{r.category}</td>
              <td>
                <span className="badge badge-warn">{r.confidence}</span>
              </td>
              <td>
                <button className="btn" style={{ marginRight: 6 }}>
                  Approve
                </button>
                <button className="btn btn-secondary">Edit</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
