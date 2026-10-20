import { useEffect, useState } from "react";

type RunRow = {
  pipeline_run_id: string;
  mode?: string;
  status?: string;
  metrics?: Record<string, number>;
};

const apiBase = () =>
  (import.meta as any).env?.VITE_API_BASE_URL || "http://localhost:8080";

const FALLBACK = [
  {
    pipeline_run_id: "run-demo-001",
    mode: "HEARTBEAT",
    status: "COMPLETED",
    metrics: { ingested: 128, classified: 120, tickets: 9 },
  },
];

export default function PipelineRuns() {
  const [runs, setRuns] = useState<RunRow[]>(FALLBACK);
  const [fromApi, setFromApi] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(`${apiBase()}/v1/runs?limit=50`);
        if (!r.ok) return;
        const data = await r.json();
        const list = Array.isArray(data) ? data : data.runs || [];
        if (list.length) {
          setRuns(list);
          setFromApi(true);
        }
      } catch {
        /* keep fallback */
      }
    })();
  }, []);

  return (
    <>
      <h2 className="page-title">Pipeline runs</h2>
      <p className="page-sub">
        Control Plane ingestion and processing history
        {fromApi ? " (live)" : " (demo data — start Control Plane for live runs)"}
      </p>
      <table>
        <thead>
          <tr>
            <th>Run ID</th>
            <th>Mode</th>
            <th>Status</th>
            <th>Ingested</th>
            <th>Classified</th>
            <th>Tickets</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.pipeline_run_id}>
              <td>
                <code>{r.pipeline_run_id}</code>
              </td>
              <td>{r.mode || "—"}</td>
              <td>
                <span
                  className={
                    r.status === "COMPLETED"
                      ? "badge badge-ok"
                      : r.status === "FAILED"
                        ? "badge badge-warn"
                        : "badge badge-info"
                  }
                >
                  {r.status || "—"}
                </span>
              </td>
              <td>{r.metrics?.ingested ?? "—"}</td>
              <td>{r.metrics?.classified ?? "—"}</td>
              <td>{r.metrics?.tickets ?? r.metrics?.ticket_candidates ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
