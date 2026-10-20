import { useEffect, useState } from "react";

const apiBase = () =>
  (import.meta as any).env?.VITE_API_BASE_URL || "http://localhost:8080";

export default function Dashboard() {
  const [metrics, setMetrics] = useState([
    { label: "Ingested (sample)", value: "—" },
    { label: "Runs completed", value: "—" },
    { label: "Tickets created", value: "—" },
    { label: "Success rate", value: "—" },
    { label: "DLQ open", value: "—" },
    { label: "SLA", value: "—" },
  ]);

  useEffect(() => {
    (async () => {
      try {
        const [sum, sla, cost] = await Promise.all([
          fetch(`${apiBase()}/v1/metrics/summary`).then((r) => (r.ok ? r.json() : null)),
          fetch(`${apiBase()}/v1/sla`).then((r) => (r.ok ? r.json() : null)),
          fetch(`${apiBase()}/v1/cost/summary`).then((r) => (r.ok ? r.json() : null)),
        ]);
        if (!sum) return;
        setMetrics([
          { label: "Ingested (sample)", value: String(sum.ingested ?? "—") },
          { label: "Runs completed", value: String(sum.completed ?? "—") },
          { label: "Tickets created", value: String(sum.tickets_created ?? "—") },
          {
            label: "Success rate",
            value:
              sum.success_rate != null ? `${Math.round(sum.success_rate * 100)}%` : "—",
          },
          {
            label: "SLA",
            value: sla?.healthy === false ? "BREACH" : sla?.healthy ? "OK" : "—",
          },
          {
            label: "Cost (USD)",
            value: cost?.total_usd != null ? `$${cost.total_usd}` : "—",
          },
        ]);
      } catch {
        /* keep placeholders */
      }
    })();
  }, []);

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
          Source → GCS raw/ → normalize → features + embeddings → classify (3 models) →
          BigQuery analytics branch · Agentic RAG → Gemini (ADC) → JiraTool → Jira
        </p>
      </div>
    </>
  );
}
