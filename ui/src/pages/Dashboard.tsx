export default function Dashboard() {
  // Demo metrics — wire to Control Plane / BigQuery APIs in production
  const metrics = [
    { label: "Ingested (24h)", value: "1,284" },
    { label: "Classified", value: "1,102" },
    { label: "Tickets created", value: "47" },
    { label: "Duplicates prevented", value: "312" },
    { label: "In review", value: "18" },
    { label: "Anomalies", value: "6" },
  ];

  return (
    <>
      <h2 className="page-title">Dashboard</h2>
      <p className="page-sub">
        Feedback → classify → Agentic RAG → Jira · project intelligent-machines
      </p>
      <div className="grid">
        {metrics.map((m) => (
          <div className="card" key={m.label}>
            <h3>{m.label}</h3>
            <div className="value">{m.value}</div>
          </div>
        ))}
      </div>
      <div className="card">
        <h3>Pipeline path</h3>
        <p style={{ margin: "0.5rem 0 0", fontSize: "0.9rem", color: "#475569" }}>
          Source → GCS raw/ → normalize → features + embeddings → classify (3
          models) → BigQuery analytics branch · Agentic RAG → Cortex/Gemini →
          JiraTool → Jira
        </p>
      </div>
    </>
  );
}
