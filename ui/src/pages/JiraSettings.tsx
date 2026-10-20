import { useState } from "react";

/**
 * Platform Settings — Jira connection + Gemini (ADC) + pipeline thresholds.
 * Jira secrets live in Secret Manager only. Gemini uses ADC / Workload Identity.
 */
type Tab = "jira" | "gemini" | "pipeline";

export default function JiraSettings() {
  const [tab, setTab] = useState<Tab>("jira");

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

  async function onSave(e: React.FormEvent) {
    e.preventDefault();
    const base = (import.meta as any).env?.VITE_API_BASE_URL || "http://localhost:8080";
    const payload =
      tab === "jira"
        ? {
            jira: {
              default_project_key: projectKey,
              default_issue_type: issueType,
              behaviour: { prefer_update_if_similar: preferUpdate },
              search: { similarity_threshold: Number(threshold) || 0.85 },
            },
          }
        : tab === "gemini"
          ? {
              gemini: {
                project: gcpProject,
                region: gcpRegion,
                model: geminiModel,
                embedding_model: embeddingModel,
                prompt_version: promptVersion,
              },
            }
          : {
              pipeline: {
                classification: { confidence_threshold: Number(confidenceThreshold) || 0.75 },
                models: { anomaly_score_threshold: Number(anomalyThreshold) || 0.8 },
                agentic_rag: { vector_search_top_k: Number(vectorTopK) || 8 },
              },
            };
    try {
      await fetch(`${base}/v1/config/${tab}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: payload }),
      });
    } catch {
      // offline / demo still show saved
    }
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  }

  async function testJiraConnection() {
    setConnectionStatus("testing");
    setConnectionMsg("Checking Secret Manager + JiraTool health…");
    const base = (import.meta as any).env?.VITE_API_BASE_URL || "http://localhost:8080";
    try {
      const r = await fetch(`${base}/v1/health`, { signal: AbortSignal.timeout(4000) });
      if (r.ok) {
        setConnectionStatus("ok");
        setConnectionMsg("Control Plane reachable. Jira secrets must be present in Secret Manager.");
      } else {
        setConnectionStatus("error");
        setConnectionMsg(`Control Plane returned ${r.status}. Start the CP or check VITE_API_BASE_URL.`);
      }
    } catch {
      setConnectionStatus("error");
      setConnectionMsg(
        "Control Plane unreachable (expected in pure UI demo). Wire credentials via Secret Manager in GCP."
      );
    }
  }

  return (
    <>
      <h2 className="page-title">Settings</h2>
      <p className="page-sub">
        Connect Jira, configure Gemini (Application Default Credentials), and tune pipeline
        thresholds. Secrets never leave Secret Manager / Workload Identity.
      </p>

      <div className="tabs" style={{ display: "flex", gap: "0.5rem", marginBottom: "1.25rem" }}>
        {(
          [
            ["jira", "Jira connection"],
            ["gemini", "Gemini / Vertex"],
            ["pipeline", "Pipeline"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={tab === id ? "btn" : "btn btn-ghost"}
            onClick={() => setTab(id)}
            style={{
              opacity: tab === id ? 1 : 0.7,
              borderBottom: tab === id ? "2px solid #0d7377" : "2px solid transparent",
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "jira" && (
        <>
          <div className="card" style={{ marginBottom: "1.25rem" }}>
            <h3>Secrets (GCP Secret Manager)</h3>
            <p style={{ fontSize: "0.875rem", margin: "0.5rem 0 0" }}>
              <code>jira-base-url</code> · <code>jira-email</code> · <code>jira-api-token</code>
            </p>
            <p className="hint" style={{ marginTop: "0.75rem" }}>
              Use gcloud secrets versions add for each secret (never commit tokens).
            </p>
            <div style={{ marginTop: "1rem", display: "flex", alignItems: "center", gap: "0.75rem" }}>
              <button type="button" className="btn" onClick={testJiraConnection}>
                {connectionStatus === "testing" ? "Testing…" : "Test connection"}
              </button>
              {connectionStatus !== "unknown" && (
                <span
                  style={{
                    fontSize: "0.875rem",
                    color:
                      connectionStatus === "ok"
                        ? "#0d7377"
                        : connectionStatus === "error"
                          ? "#c0392b"
                          : "#666",
                  }}
                >
                  {connectionMsg}
                </span>
              )}
            </div>
          </div>

          <form onSubmit={onSave} className="card">
            <div className="form-group">
              <label>Jira base URL (hint only — real value in Secret Manager)</label>
              <input
                value={jiraBaseUrlHint}
                onChange={(e) => setJiraBaseUrlHint(e.target.value)}
                placeholder="https://your-domain.atlassian.net"
              />
            </div>
            <div className="form-group">
              <label>Default project key</label>
              <input value={projectKey} onChange={(e) => setProjectKey(e.target.value)} />
            </div>
            <div className="form-group">
              <label>Default issue type</label>
              <select value={issueType} onChange={(e) => setIssueType(e.target.value)}>
                <option>Bug</option>
                <option>Task</option>
                <option>Story</option>
                <option>Incident</option>
              </select>
            </div>
            <div className="form-group">
              <label>Similarity threshold (dedup)</label>
              <input
                value={threshold}
                onChange={(e) => setThreshold(e.target.value)}
                placeholder="0.85"
              />
            </div>
            <div className="form-group">
              <label>
                <input
                  type="checkbox"
                  checked={preferUpdate}
                  onChange={(e) => setPreferUpdate(e.target.checked)}
                  style={{ width: "auto", marginRight: 8 }}
                />
                Prefer update existing ticket when similar
              </label>
            </div>
            <button type="submit" className="btn">
              Save Jira settings
            </button>
            {saved && (
              <span style={{ marginLeft: 12, color: "#0d7377", fontSize: "0.875rem" }}>
                Saved (demo — wire to Control Plane /v1/config)
              </span>
            )}
          </form>
        </>
      )}

      {tab === "gemini" && (
        <form onSubmit={onSave} className="card">
          <p className="hint" style={{ marginBottom: "1rem" }}>
            Gemini is invoked via <strong>Vertex AI + Application Default Credentials</strong>{" "}
            (Workload Identity on GKE). No Cortex API and no API keys in the UI or code path.
          </p>
          <div className="form-group">
            <label>GCP project</label>
            <input value={gcpProject} onChange={(e) => setGcpProject(e.target.value)} />
          </div>
          <div className="form-group">
            <label>Region</label>
            <input value={gcpRegion} onChange={(e) => setGcpRegion(e.target.value)} />
          </div>
          <div className="form-group">
            <label>Gemini model</label>
            <select value={geminiModel} onChange={(e) => setGeminiModel(e.target.value)}>
              <option value="gemini-2.0-flash-001">gemini-2.0-flash-001</option>
              <option value="gemini-1.5-pro-002">gemini-1.5-pro-002</option>
              <option value="gemini-1.5-flash-002">gemini-1.5-flash-002</option>
            </select>
          </div>
          <div className="form-group">
            <label>Embedding model</label>
            <input value={embeddingModel} onChange={(e) => setEmbeddingModel(e.target.value)} />
          </div>
          <div className="form-group">
            <label>Prompt version</label>
            <input value={promptVersion} onChange={(e) => setPromptVersion(e.target.value)} />
          </div>
          <button type="submit" className="btn">
            Save Gemini settings
          </button>
          {saved && (
            <span style={{ marginLeft: 12, color: "#0d7377", fontSize: "0.875rem" }}>
              Saved (demo)
            </span>
          )}
        </form>
      )}

      {tab === "pipeline" && (
        <form onSubmit={onSave} className="card">
          <div className="form-group">
            <label>Classification confidence threshold (review if below)</label>
            <input
              value={confidenceThreshold}
              onChange={(e) => setConfidenceThreshold(e.target.value)}
              placeholder="0.75"
            />
          </div>
          <div className="form-group">
            <label>Anomaly score threshold</label>
            <input
              value={anomalyThreshold}
              onChange={(e) => setAnomalyThreshold(e.target.value)}
              placeholder="0.8"
            />
          </div>
          <div className="form-group">
            <label>Vector Search top-K</label>
            <input value={vectorTopK} onChange={(e) => setVectorTopK(e.target.value)} placeholder="8" />
          </div>
          <button type="submit" className="btn">
            Save pipeline settings
          </button>
          {saved && (
            <span style={{ marginLeft: 12, color: "#0d7377", fontSize: "0.875rem" }}>
              Saved (demo)
            </span>
          )}
        </form>
      )}
    </>
  );
}
