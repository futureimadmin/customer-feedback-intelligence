"""
Stage 8 — Agentic RAG & ticket intelligence.

Builds grounded context from classified record + feature/cluster/anomaly,
calls Gemini via Vertex AI using Application Default Credentials (ADC),
then dispatches to Control Plane → JiraTool.

No Cortex API. Falls back to a deterministic template Ticket Candidate when
Gemini is unavailable (local/dev without ADC or Vertex access).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any

import httpx

from common import control_plane_url, load_pipeline_config, report_stage, short_id, utcnow

logger = logging.getLogger("data-plane.agentic_rag")


def dedup_key(classified: dict[str, Any]) -> str:
    product = (classified.get("product") or "unknown").lower()
    category = (classified.get("category") or "other").lower()
    cluster = (classified.get("cluster") or {}).get("clusterId") or ""
    month = (classified.get("classifiedAt") or utcnow())[:7]
    raw = f"{product}|{category}|{cluster}|{month}"
    return f"dk-{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def retrieve_similar_issues(text: str, top_k: int = 8) -> list[dict[str, Any]]:
    """Query Vertex AI Vector Search when INDEX_ENDPOINT / DEPLOYED_INDEX_ID are set."""
    endpoint_name = os.environ.get("VECTOR_INDEX_ENDPOINT")
    deployed_id = os.environ.get("VECTOR_DEPLOYED_INDEX_ID")
    if not endpoint_name or not deployed_id:
        return []

    project = os.environ.get("GCP_PROJECT", "intelligent-machines")
    location = os.environ.get("GCP_REGION", "us-central1")
    embedding_model = (
        os.environ.get("EMBEDDING_MODEL")
        or load_pipeline_config().get("models", {}).get("embedding_model", "text-embedding-004")
    )

    try:
        import vertexai
        from vertexai.language_models import TextEmbeddingModel
        from google.cloud import aiplatform

        vertexai.init(project=project, location=location)
        model = TextEmbeddingModel.from_pretrained(embedding_model)
        emb = model.get_embeddings([text or ""])[0].values

        aiplatform.init(project=project, location=location)
        index_endpoint = aiplatform.MatchingEngineIndexEndpoint(index_endpoint_name=endpoint_name)
        response = index_endpoint.find_neighbors(
            deployed_index_id=deployed_id,
            queries=[list(emb)],
            num_neighbors=top_k,
        )
        similar: list[dict[str, Any]] = []
        for neighbors in response or []:
            for n in neighbors:
                similar.append(
                    {
                        "id": getattr(n, "id", None),
                        "distance": getattr(n, "distance", None),
                        "metadata": getattr(n, "restricts", None) or {},
                    }
                )
        logger.info("Vector Search returned %d neighbors", len(similar))
        return similar
    except Exception as e:
        logger.warning("Vector Search unavailable: %s", e)
        return []


def build_grounded_context(classified: dict[str, Any]) -> dict[str, Any]:
    cfg = load_pipeline_config()
    top_k = int(cfg.get("agentic_rag", {}).get("vector_search_top_k", 8))
    text = classified.get("cleanedText") or ""
    similar = retrieve_similar_issues(text, top_k=top_k)
    return {
        "classified": {
            "businessUnit": classified.get("businessUnit"),
            "category": classified.get("category"),
            "confidence": classified.get("confidence"),
        },
        "text": text,
        "anomaly": classified.get("anomaly"),
        "cluster": classified.get("cluster"),
        "similarIssues": similar,
        "openTickets": [],
        "policy": "Prefer update existing ticket when similarity > 0.85 and same product",
        "promptVersion": cfg.get("agentic_rag", {}).get("prompt_version", "ticket-intel-v4.2"),
    }


def template_ticket_candidate(classified: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    text = classified.get("cleanedText") or ""
    bu = classified.get("businessUnit") or "General Support"
    cat = classified.get("category") or "Other"
    product = classified.get("product") or "Product"
    severity = classified.get("suggestedSeverity") or "Medium"
    anomaly = classified.get("anomaly") or {}

    summary = f"{product} {cat.lower()} — {text[:80]}"
    if len(text) > 80:
        summary = summary[:77] + "..."

    description_parts = [
        text,
        "",
        f"Business unit: {bu}",
        f"Category: {cat}",
        f"Confidence: {classified.get('confidence')}",
    ]
    if anomaly.get("isAnomaly"):
        description_parts.append(
            f"Anomaly: {anomaly.get('anomalyType')} (score={anomaly.get('anomalyScore')}) — "
            f"{anomaly.get('supportingEvidence') or ''}"
        )

    labels = [str(cat).lower().replace(" ", "-")]
    if product:
        labels.append(str(product).lower().replace(" ", "-"))
    if anomaly.get("isAnomaly"):
        labels.append("anomaly")

    return {
        "ticketCandidateId": f"tc-{short_id()}",
        "action": "create_or_update",
        "summary": summary,
        "description": "\n".join(description_parts),
        "issueType": "Bug" if cat == "Crash" else "Task",
        "priority": "High" if severity == "High" or anomaly.get("isAnomaly") else "Medium",
        "severity": severity,
        "businessUnit": bu,
        "labels": labels,
        "deduplicationKey": dedup_key(classified),
        "suggestedExistingKey": None,
        "confidence": classified.get("confidence"),
        "modelVersion": "template-v1",
        "promptVersion": context.get("promptVersion"),
        "evidenceRefs": [classified.get("classifiedId"), classified.get("parentFeatureId")],
        "createdAt": utcnow(),
    }


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


def call_gemini(context: dict[str, Any], classified: dict[str, Any]) -> dict[str, Any] | None:
    """Invoke Gemini via Vertex AI using Application Default Credentials."""
    project = os.environ.get("GCP_PROJECT", "intelligent-machines")
    location = os.environ.get("GCP_REGION", "us-central1")
    model_name = os.environ.get(
        "GEMINI_MODEL",
        load_pipeline_config().get("agentic_rag", {}).get("gemini_model", "gemini-2.0-flash-001"),
    )

    prompt = f"""You are an enterprise ticket-intelligence agent. Given classified customer feedback
and grounded context, produce a single JSON object for a Jira ticket candidate.

Rules:
- Prefer updating an existing ticket when similarity policy allows.
- Output ONLY valid JSON. No markdown commentary outside a JSON object.
- Required keys: summary, description, issueType, priority, severity, businessUnit, labels (array),
  deduplicationKey, suggestedExistingKey (string or null), confidence (0-1), modelVersion, promptVersion.

Context:
{json.dumps(context, indent=2)}

Classified record:
{json.dumps(classified, indent=2)}
"""

    try:
        import vertexai
        from vertexai.generative_models import GenerativeModel, GenerationConfig

        vertexai.init(project=project, location=location)
        model = GenerativeModel(model_name)
        response = model.generate_content(
            prompt,
            generation_config=GenerationConfig(
                temperature=0.2,
                max_output_tokens=2048,
                response_mime_type="application/json",
            ),
        )
        raw = response.text if hasattr(response, "text") else str(response)
        parsed = _extract_json(raw)
        if not parsed:
            logger.warning("Gemini returned non-JSON; falling back to template")
            return None

        if "ticketCandidateId" not in parsed:
            parsed["ticketCandidateId"] = f"tc-{short_id()}"
        if "deduplicationKey" not in parsed:
            parsed["deduplicationKey"] = dedup_key(classified)
        if "createdAt" not in parsed:
            parsed["createdAt"] = utcnow()
        if "modelVersion" not in parsed:
            parsed["modelVersion"] = model_name
        if "promptVersion" not in parsed:
            parsed["promptVersion"] = context.get("promptVersion")
        if "action" not in parsed:
            parsed["action"] = "create_or_update"
        if "labels" not in parsed or not isinstance(parsed["labels"], list):
            parsed["labels"] = []
        if "evidenceRefs" not in parsed:
            parsed["evidenceRefs"] = [
                classified.get("classifiedId"),
                classified.get("parentFeatureId"),
            ]

        logger.info("Gemini ticket candidate produced via ADC (model=%s)", model_name)
        return parsed
    except Exception as e:
        logger.warning("Gemini (Vertex ADC) call failed: %s", e)
        return None


def run_agentic_rag(pipeline_run_id: str, classified: dict[str, Any]) -> dict[str, Any]:
    if classified.get("reviewRequired"):
        report_stage(
            pipeline_run_id,
            "AGENTIC_RAG",
            "SKIPPED",
            artifact_ids=[classified.get("classifiedId", "")],
        )
        return {"status": "skipped", "reason": "reviewRequired"}

    report_stage(pipeline_run_id, "AGENTIC_RAG", "RUNNING")
    try:
        context = build_grounded_context(classified)
        candidate = call_gemini(context, classified) or template_ticket_candidate(classified, context)

        cp = control_plane_url()
        with httpx.Client(timeout=30.0) as client:
            tc_resp = client.post(
                f"{cp}/v1/runs/{pipeline_run_id}/ticket-candidates",
                json={
                    "classified_id": classified.get("classifiedId"),
                    "deduplication_key": candidate.get("deduplicationKey"),
                },
            )
            if tc_resp.status_code < 400:
                tc = tc_resp.json()
                candidate["ticketCandidateId"] = tc.get(
                    "ticket_candidate_id", candidate["ticketCandidateId"]
                )

            dispatch = client.post(
                f"{cp}/v1/jira/dispatch",
                json={
                    "ticket_candidate_id": candidate["ticketCandidateId"],
                    "summary": candidate["summary"],
                    "description": candidate["description"],
                    "priority": candidate.get("priority", "Medium"),
                    "labels": candidate.get("labels", []),
                    "suggested_existing_key": candidate.get("suggestedExistingKey"),
                    "issue_type": candidate.get("issueType"),
                },
            )
            jira_result = (
                dispatch.json() if dispatch.status_code < 500 else {"error": dispatch.text}
            )

        report_stage(
            pipeline_run_id,
            "AGENTIC_RAG",
            "COMPLETED",
            artifact_ids=[candidate["ticketCandidateId"]],
            parent_artifact_ids=[classified.get("classifiedId", "")],
            metrics={"ticket_candidates": 1},
        )
        jira_status = (jira_result.get("jira_tool") or jira_result).get("status", "")
        if jira_status in ("JIRA_CREATED", "EXISTING_TICKET", "EXISTING_TICKET_UPDATED"):
            report_stage(
                pipeline_run_id,
                "JIRA",
                "COMPLETED",
                artifact_ids=[(jira_result.get("jira_tool") or {}).get("jira_key", "")],
                metrics={"tickets": 1},
            )
        else:
            report_stage(pipeline_run_id, "JIRA", "FAILED", error=str(jira_result))

        return {"candidate": candidate, "jira": jira_result}
    except Exception as e:
        report_stage(pipeline_run_id, "AGENTIC_RAG", "FAILED", error=str(e))
        raise


if __name__ == "__main__":
    cls = {
        "classifiedId": "cls-demo",
        "parentFeatureId": "feat-demo",
        "businessUnit": "Mobile Engineering",
        "category": "Crash",
        "suggestedSeverity": "High",
        "confidence": 0.93,
        "reviewRequired": False,
        "cleanedText": "App crashes every time I try to checkout on Android",
        "product": "Checkout",
        "anomaly": {
            "isAnomaly": True,
            "anomalyScore": 0.91,
            "anomalyType": "volume_spike",
            "supportingEvidence": "38 similar in 1h",
        },
        "cluster": {"clusterId": "clu-checkout-crash"},
        "classifiedAt": "2026-09-18T10:18:40Z",
    }
    print(json.dumps(template_ticket_candidate(cls, build_grounded_context(cls)), indent=2))
