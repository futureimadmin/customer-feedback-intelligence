# Documentation

| Document | Description |
|----------|-------------|
| [CFI_Architecture_Current_vs_Target_Vertex_MLOps.docx](./CFI_Architecture_Current_vs_Target_Vertex_MLOps.docx) | HLD addendum: §3.1 Current State Architecture, §11.2 Target State with Vertex MLOps |
| [Customer_Feedback_Intelligence_LLD_v1.1.docx](./Customer_Feedback_Intelligence_LLD_v1.1.docx) | Low-Level Design v1.1 (stages, models, Agentic RAG, PipelineJob launch) |
| [target_state_architecture.png](./target_state_architecture.png) | Target-state architecture diagram |
| [CONTROL_PLANE_RUNTIME.md](./CONTROL_PLANE_RUNTIME.md) | How modes launch (transitional Pub/Sub + target Vertex) |
| [DEPLOY.md](./DEPLOY.md) | Deployment notes |

**Target state:** Control Plane submits Vertex `PipelineJob`; Data Plane stages run as KFP containers; Agentic RAG → Cortex → JiraTool remains outside training path; no Dataflow; no Kafka on Jira path.
