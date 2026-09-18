import { useState } from "react";

/**
 * Non-secret Jira settings UI. Credentials are never entered here in production;
 * they live in Secret Manager (jira-base-url, jira-email, jira-api-token).
 */
export default function JiraSettings() {
  const [projectKey, setProjectKey] = useState("MOB");
  const [issueType, setIssueType] = useState("Bug");
  const [preferUpdate, setPreferUpdate] = useState(true);
  const [threshold, setThreshold] = useState("0.85");
  const [saved, setSaved] = useState(false);

  function onSave(e: React.FormEvent) {
    e.preventDefault();
    // POST to Control Plane config API in production
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  }

  return (
    <>
      <h2 className="page-title">Jira connection settings</h2>
      <p className="page-sub">
        Non-secret defaults. API token and base URL are stored in GCP Secret
        Manager (project intelligent-machines).
      </p>

      <div className="card" style={{ marginBottom: "1.25rem" }}>
        <h3>Secrets (Secret Manager)</h3>
        <p style={{ fontSize: "0.875rem", margin: "0.5rem 0 0" }}>
          <code>jira-base-url</code> · <code>jira-email</code> ·{" "}
          <code>jira-api-token</code>
        </p>
        <p className="hint">
          gcloud secrets versions add jira-api-token --data-file=- <<<
          "YOUR_TOKEN"
        </p>
      </div>

      <form onSubmit={onSave} className="card">
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
          Save settings
        </button>
        {saved && (
          <span style={{ marginLeft: 12, color: "#0d7377", fontSize: "0.875rem" }}>
            Saved (demo)
          </span>
        )}
      </form>
    </>
  );
}
