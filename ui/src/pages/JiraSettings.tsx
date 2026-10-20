import { useEffect, useState } from "react";

/**
 * Platform Settings — Jira connection + Gemini (ADC) + pipeline thresholds.
 * Loads from Control Plane /v1/config/* on mount; persists via PUT.
 */
type Tab = "jira" | "gemini" | "pipeline";

const apiBase = () =>
  (import.meta as any).env?.VITE_API_BASE_URL || "http://localhost:8080";

export default function JiraSettings() {
  const [tab, setTab] = useState<Tab>("jira");
  const [loading, setLoading] = useState(true);

  const [projectKey, setProjectKey] = useState("MOB");
  const [issueType, setIssueType] = useState("Bug");
  const [preferUpdate, setPreferUpdate] = useState(true);
  const [threshold, setThreshold] = useState("0.85");
  const [jiraBaseUrlHint, setJiraBaseUrlHint] = useState("https://your-domain.atlassian.net");
  const [connectionStatus, setConnectionStatus] = useState<
    "unknown" | "testing" | "ok" | "error"
  >("unknown");
  const [connectionMsg, setConnectionMsg] = useState("");

  const [gcpProject, setGcpProject] = useState("intelligent-machines");
  const [gcpRegion, setGcpRegion] = useState("us-central1");
  const [geminiModel, setGeminiModel] = useState("gemini-2.0-flash-001");
  const [embeddingModel, setEmbeddingModel] = useState("text-embedding-004");
  const [promptVersion, setPromptVersion] = useState("ticket-intel-v4.2");

  const [confidenceThreshold, setConfidenceThreshold] = useState("0.75");
  const [anomalyThreshold, setAnomalyThreshold] = useState("0.8");
  const [vectorTopK, setVectorTopK] = useState("8");

  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const base = apiBase();
      try {
        const [jira, gemini, pipeline] = await Promise.all([
          fetch(`${base}/v1/config/jira`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
          fetch(`${base}/v1/config/gemini`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
          fetch(`${base}/v1/config/pipeline`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
        ]);
        if (cancelled) return;
        const j = jira?.value?.jira || jira?.value || jira || {};
        if (j.default_project_key) setProjectKey(j.default_project_key);
        if (j.default_issue_type) setIssueType(j.default_issue_type);
        if (j.behaviour?.prefer_update_if_similar != null)
          setPreferUpdate(!!j.behaviour.prefer_update_if_similar);
        if (j.search?.similarity_threshold != null)
          setThreshold(String(j.search.similarity_threshold));
        if (j.base_url_hint) setJiraBaseUrlHint(j.base_url_hint);

        const g = gemini?.value?.gemini || gemini?.value || gemini || {};
        if (g.project) setGcpProject(g.project);
        if (g.region) setGcpRegion(g.region);
        if (g.model) setGeminiModel(g.model);
        if (g.embedding_model) setEmbeddingModel(g.embedding_model);
        if (g.prompt_version) setPromptVersion(g.prompt_version);

        const p = pipeline?.value?.pipeline || pipeline?.value || pipeline || {};
        if (p.classification?.confidence_threshold != null)
          setConfidenceThreshold(String(p.classification.confidence_threshold));
        if (p.models?.anomaly_score_threshold != null)
          setAnomalyThreshold(String(p.models.anomaly_score_threshold));
        if (p.agentic_rag?.vector_search_top_k != null)
          setVectorTopK(String(p.agentic_rag.vector_search_top_k));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function onSave(e: React.FormEvent) {
    e.preventDefault();
    setSaved(false);
    const base = apiBase();
    const payload =
      tab === "jira"
        ? {
            default_project_key: projectKey,
            default_issue_type: issueType,
            behaviour: { prefer_update_if_similar: preferUpdate },
            search: { similarity_threshold: parseFloat(threshold) || 0.85 },
            base_url_hint: jiraBaseUrlHint,
          }
        : tab === "gemini"
          ? {
              project: gcpProject,
              region: gcpRegion,
              model: geminiModel,
              embedding_model: embeddingModel,
              prompt_version: promptVersion,
            }
          : {
              classification: { confidence_threshold: parseFloat(confidenceThreshold) || 0.75 },
              models: { anomaly_score_threshold: parseFloat(anomalyThreshold) || 0.8 },
              agentic_rag: { vector_search_top_k: parseInt(vectorTopK, 10) || 8 },
            };
    try {
      await fetch(`${base}/v1/config/${tab}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch {
      /* offline */
    }
  }

  async function testJiraConnection() {
    setConnectionStatus("testing");
    setConnectionMsg("");
    const base = apiBase();
    try {
      const r = await fetch(`${base}/v1/config/jira/test`, { method: "POST" });
      if (r.ok) {
        setConnectionStatus("ok");
        setConnectionMsg("JiraTool reachable (credentials checked server-side)");
      } else {
        setConnectionStatus("error");
        setConnectionMsg(`HTTP ${r.status}`);
      }
    } catch (e: any) {
      setConnectionStatus("error");
      setConnectionMsg(e.message || "unreachable");
    }
  }

  if (loading) {
    return (
      <>
        <h2 className="page-title">Settings</h2>
        <p className="page-sub">Loading configuration…</p>
      </>
    );
  }

  return (
    <>
      <h2 className="page-title">Settings</h2>
      <p className="page-sub">
        Jira connection, Gemini (Vertex ADC), and pipeline thresholds — persisted via Control Plane
      </p>

      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        {(["jira", "gemini", "pipeline"] as Tab[]).map((t) => (
          <button
            key={t}
            type="button"
            className={tab === t ? "btn" : "btn btn-secondary"}
            onClick={() => setTab(t)}
          >
            {t === "jira" ? "Jira" : t === "gemini" ? "Gemini / Vertex" : "Pipeline"}
          </button>
        ))}
      </div>

      <form onSubmit={onSave} className="card" style={{ maxWidth: 560 }}>
        {tab === "jira" && (
          <>
            <label>
              Project key
              <input value={projectKey} onChange={(e) => setProjectKey(e.target.value)} />
            </label>
            <label>
              Default issue type
              <input value={issueType} onChange={(e) => setIssueType(e.target.value)} />
            </label>
            <label>
              Similarity threshold
              <input value={threshold} onChange={(e) => setThreshold(e.target.value)} />
            </label>
            <label>
              Base URL (hint)
              <input value={jiraBaseUrlHint} onChange={(e) => setJiraBaseUrlHint(e.target.value)} />
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input
                type="checkbox"
                checked={preferUpdate}
                onChange={(e) => setPreferUpdate(e.target.checked)}
              />
              Prefer update existing similar issues
            </label>
            <div style={{ marginTop: 12 }}>
              <button type="button" className="btn btn-secondary" onClick={testJiraConnection}>
                Test connection
              </button>
              {connectionStatus !== "unknown" && (
                <span style={{ marginLeft: 12, fontSize: "0.85rem" }}>
                  {connectionStatus === "testing"
                    ? "Testing…"
                    : connectionStatus === "ok"
                      ? `✓ ${connectionMsg}`
                      : `✗ ${connectionMsg}`}
                </span>
              )}
            </div>
            <p className="hint" style={{ marginTop: 12 }}>
              API token is stored in Secret Manager — never in this UI.
            </p>
          </>
        )}

        {tab === "gemini" && (
          <>
            <label>
              GCP project
              <input value={gcpProject} onChange={(e) => setGcpProject(e.target.value)} />
            </label>
            <label>
              Region
              <input value={gcpRegion} onChange={(e) => setGcpRegion(e.target.value)} />
            </label>
            <label>
              Gemini model
              <input value={geminiModel} onChange={(e) => setGeminiModel(e.target.value)} />
            </label>
            <label>
              Embedding model
              <input value={embeddingModel} onChange={(e) => setEmbeddingModel(e.target.value)} />
            </label>
            <label>
              Prompt version
              <input value={promptVersion} onChange={(e) => setPromptVersion(e.target.value)} />
            </label>
            <p className="hint">Auth: Application Default Credentials / Workload Identity (no API keys).</p>
          </>
        )}

        {tab === "pipeline" && (
          <>
            <label>
              Classification confidence threshold
              <input
                value={confidenceThreshold}
                onChange={(e) => setConfidenceThreshold(e.target.value)}
              />
            </label>
            <label>
              Anomaly score threshold
              <input value={anomalyThreshold} onChange={(e) => setAnomalyThreshold(e.target.value)} />
            </label>
            <label>
              Vector Search top-k
              <input value={vectorTopK} onChange={(e) => setVectorTopK(e.target.value)} />
            </label>
          </>
        )}

        <div style={{ marginTop: 16, display: "flex", gap: 12, alignItems: "center" }}>
          <button type="submit" className="btn">
            Save {tab}
          </button>
          {saved && <span style={{ color: "#2e7d32", fontSize: "0.9rem" }}>Saved</span>}
        </div>
      </form>
    </>
  );
}
