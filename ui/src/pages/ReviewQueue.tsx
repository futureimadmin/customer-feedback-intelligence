import { useEffect, useState } from "react";

type ReviewRow = {
  review_id: string;
  classified_id: string;
  text: string;
  business_unit?: string | null;
  category?: string | null;
  confidence?: number | null;
  status?: string;
  reason?: string | null;
};

const apiBase = () =>
  (import.meta as any).env?.VITE_API_BASE_URL || "http://localhost:8080";

export default function ReviewQueue() {
  const [items, setItems] = useState<ReviewRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [fromApi, setFromApi] = useState(false);

  async function load() {
    try {
      const r = await fetch(`${apiBase()}/v1/review-queue?status=PENDING&limit=50`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      const list = Array.isArray(data) ? data : data.items || [];
      setItems(list);
      setFromApi(true);
      setError(null);
    } catch (e: any) {
      setError(e.message || "Control Plane unreachable");
      setFromApi(false);
      setItems([
        {
          review_id: "rev-demo-1",
          classified_id: "cls-e7f3a2b1",
          text: "App crashes every time I try to checkout on Android",
          business_unit: "Mobile Engineering",
          category: "Crash",
          confidence: 0.62,
          reason: "Below threshold",
          status: "PENDING",
        },
      ]);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function act(id: string, action: "approve" | "reject") {
    setBusy(id);
    try {
      await fetch(`${apiBase()}/v1/review-queue/${encodeURIComponent(id)}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      await load();
    } catch {
      setItems((prev) => prev.filter((x) => x.review_id !== id));
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <h2 className="page-title">Review queue</h2>
      <p className="page-sub">
        Low-confidence classifications awaiting human approval before ticket generation
        {fromApi ? " (live)" : " (demo fallback)"}
      </p>
      {error && !fromApi && (
        <p className="hint" style={{ color: "#8b6914" }}>
          {error} — showing sample items.
        </p>
      )}
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
          {items.length === 0 && (
            <tr>
              <td colSpan={6} style={{ color: "#64748b" }}>
                Queue empty — no pending reviews.
              </td>
            </tr>
          )}
          {items.map((r) => (
            <tr key={r.review_id}>
              <td>
                <code>{r.review_id}</code>
              </td>
              <td>{r.text}</td>
              <td>{r.business_unit || "—"}</td>
              <td>{r.category || "—"}</td>
              <td>
                <span className="badge badge-warn">
                  {r.confidence != null ? r.confidence.toFixed(2) : "—"}
                </span>
              </td>
              <td>
                <button
                  className="btn"
                  style={{ marginRight: 6 }}
                  disabled={busy === r.review_id}
                  onClick={() => act(r.review_id, "approve")}
                >
                  Approve
                </button>
                <button
                  className="btn btn-secondary"
                  disabled={busy === r.review_id}
                  onClick={() => act(r.review_id, "reject")}
                >
                  Reject
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
