# Documentation

| Document | Description |
|----------|-------------|
| [ARCHITECTURE_CURRENT_VS_TARGET.md](./ARCHITECTURE_CURRENT_VS_TARGET.md) | **§3.1 Current State** and **§11.2 Target State** (Vertex MLOps) |
| [LLD_v1.1.md](./LLD_v1.1.md) | **Low-Level Design v1.1** — stages, models, Agentic RAG, PipelineJob launch |
| [CONTROL_PLANE_RUNTIME.md](./CONTROL_PLANE_RUNTIME.md) | How modes launch (Pub/Sub transitional + target Vertex) |
| [DEPLOY.md](./DEPLOY.md) | Deployment notes |

### Binary DOCX (generated locally; attach in releases)

| File | Path in workspace |
|------|-------------------|
| Architecture addendum + diagram | `artifacts/CFI_Architecture_Current_vs_Target_Vertex_MLOps.docx` |
| LLD v1.1 + diagram | `artifacts/Customer_Feedback_Intelligence_LLD_v1.1.docx` |
| Target diagram PNG | `artifacts/target_state_architecture.png` |

**Target state summary:** Control Plane submits Vertex `PipelineJob`; Data Plane stages run as KFP containers; Agentic RAG → Cortex → JiraTool stays outside the training path; **no Dataflow**; **no Kafka** on the Jira path.
