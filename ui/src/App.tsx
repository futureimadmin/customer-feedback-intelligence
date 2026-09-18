import { NavLink, Route, Routes } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import JiraSettings from "./pages/JiraSettings";
import ReviewQueue from "./pages/ReviewQueue";
import PipelineRuns from "./pages/PipelineRuns";

export default function App() {
  return (
    <div className="layout">
      <aside className="sidebar">
        <h1>Customer Feedback Intelligence</h1>
        <nav>
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          <NavLink to="/runs">Pipeline runs</NavLink>
          <NavLink to="/review">Review queue</NavLink>
          <NavLink to="/settings/jira">Jira settings</NavLink>
        </nav>
        <p style={{ marginTop: "2rem", fontSize: "0.7rem", opacity: 0.7 }}>
          GCP: intelligent-machines
          <br />
          Region: us-central1
          <br />
          Bucket: customer-feedback
        </p>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/runs" element={<PipelineRuns />} />
          <Route path="/review" element={<ReviewQueue />} />
          <Route path="/settings/jira" element={<JiraSettings />} />
        </Routes>
      </main>
    </div>
  );
}
