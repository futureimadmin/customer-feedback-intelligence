import { useEffect, useState } from "react";

type ModelRow = {
  model_id: string;
  version: string;
  task: string;
  status: string;
  metrics?: Record<string, number>;
  dataset_version?: string | null;
  endpoint?: string | null;
  created_at?: string;
};

const apiBase = () =>
  (import.meta as any).env?.VITE_API_BASE_URL || "http://localhost:8080";

export default function Models() {
  const [models, setModels] = useState<ModelRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function load() {
    try {
      const r = await fetch(`${apiBase()}/v1/models`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setModels(Array.isArray(data) ? data : data.models || []);
      setError(null);
    } catch (e: any) {
      setError(e.message || "Failed to load models");
      setModels([]);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function promote(m: ModelRow) {
    setBusy(`${m.model_id}@${m.version}`);
    try {
      await fetch(
        `${apiBase()}/v1/models/${encodeURIComponent(m.model_id)}/versions/${encodeURIComponent(m.version)}/promote`,
        { method: "POST" }
      );
      await load();
    } catch {
      /* ignore */
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <h2 className="page-title">Model registry</h2>
      <p className="page-sub">
        Task-specific models (classifier, clustering, anomaly). Promote candidates to production.
      </p>
      {error && (
        <p className="hint" style={{ color: "#c0392b" }}>
          {error} — showing empty list until Control Plane is reachable.
        </p>
      )}
      <table>
        <thead>
          <tr>
            <th>Model</th>
            <th>Version</th>
            <th>Task</th>
            <th>Status</th>
            <th>Metrics</th>
            <th>Dataset</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {models.length === 0 && (
            <tr>
              <td colSpan={7} style={{ color: "#64748b" }}>
                No registered models yet. Run{" "}
                <code>python -m training</code> in the data-plane to register a candidate.
              </td>
            </tr>
          )}
          {models.map((m) => (
            <tr key={`${m.model_id}-${m.version}`}>
              <td>
                <code>{m.model_id}</code>
              </td>
              <td>{m.version}</td>
              <td>{m.task}</td>
              <td>
                <span
                  className={
                    m.status === "PRODUCTION"
                      ? "badge badge-ok"
                      : m.status === "CANDIDATE"
                        ? "badge badge-info"
                        : "badge badge-warn"
                  }
                >
                  {m.status}
                </span>
              </td>
              <td style={{ fontSize: "0.8rem" }}>
                {m.metrics
                  ? Object.entries(m.metrics)
                      .map(([k, v]) => `${k}=${v}`)
                      .join(", ")
                  : "—"}
              </td>
              <td style={{ fontSize: "0.8rem" }}>{m.dataset_version || "—"}</td>
              <td>
                {m.status !== "PRODUCTION" && (
                  <button
                    type="button"
                    className="btn"
                    disabled={busy === `${m.model_id}@${m.version}`}
                    onClick={() => promote(m)}
                  >
                    Promote
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
