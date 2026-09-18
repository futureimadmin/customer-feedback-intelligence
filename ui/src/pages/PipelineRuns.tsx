const RUNS = [
  {
    id: "run-20260918-001",
    mode: "Heartbeat",
    status: "COMPLETED",
    ingested: 128,
    classified: 120,
    tickets: 9,
  },
  {
    id: "run-20260918-000",
    mode: "Initial Full",
    status: "COMPLETED",
    ingested: 5400,
    classified: 5210,
    tickets: 210,
  },
  {
    id: "run-20260917-014",
    mode: "Heartbeat",
    status: "FAILED",
    ingested: 40,
    classified: 12,
    tickets: 0,
  },
];

export default function PipelineRuns() {
  return (
    <>
      <h2 className="page-title">Pipeline runs</h2>
      <p className="page-sub">Control Plane ingestion and processing history</p>
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
          {RUNS.map((r) => (
            <tr key={r.id}>
              <td>
                <code>{r.id}</code>
              </td>
              <td>{r.mode}</td>
              <td>
                <span
                  className={
                    r.status === "COMPLETED"
                      ? "badge badge-ok"
                      : "badge badge-warn"
                  }
                >
                  {r.status}
                </span>
              </td>
              <td>{r.ingested}</td>
              <td>{r.classified}</td>
              <td>{r.tickets}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
