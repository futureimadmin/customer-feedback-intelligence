"""
Stage 8 — Agentic RAG & ticket intelligence.

Builds grounded context from classified record + feature/cluster/anomaly,
optionally calls Cortex/Gemini (CORTEX_API_URL), then dispatches to Control
Plane → JiraTool.

Without Cortex, produces a deterministic Ticket Candidate from templates.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

import httpx

from common import control_plane_url, load_pipeline_config, report_stage, short_id, utcnow

logger = logging.getLogger("data-plane.agentic_rag")


def dedup_key(classified: dict[str, Any]) -> str:
    product = (classified.get("product") or "unknown").lower()
    category = (classified.get("category") or "other").lower()
    cluster = (classified.get("cluster") or {}).get("clusterId") or ""
    # Time-bucket by month for deterministic key
    month = (classified.get("classifiedAt") or utcnow())[:7]
    raw = f"{product}|{category}|{cluster}|{month}"
    return f"dk-{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def build_grounded_context(classified: dict[str, Any]) -> dict[str, Any]:
    cfg = load_pipeline_config()
    return {
        "classified": {
            "businessUnit": classified.get("businessUnit"),
            "category": classified.get("category"),
            "confidence": classified.get("confidence"),
        },
        "text": classified.get("cleanedText"),
        "anomaly": classified.get("anomaly"),
        "cluster": classified.get("cluster"),
        "similarIssues": [],  # filled by Vector Search in production
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
            f"Anomaly: {anomaly.get('anomalyType')} (score={anomaly.get('anomalyScore')}) — {anomaly.get('supportingEvidence')}"
        )
    if classified.get("cluster"):
        description_parts.append(f"Cluster: {classified['cluster'].get('clusterId')}")

    labels = [cat.lower().replace(" ", "-"), "auto-generated"]
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


def call_cortex(context: dict[str, Any], classified: dict[str, Any]) -> dict[str, Any] | None:
    url = os.environ.get("CORTEX_API_URL")
    if not url:
        return None
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                url.rstrip("/") + "/v1/ticket-intelligence",
                json={"context": context, "classified": classified},
            )
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("Cortex call failed: %s", e)
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
        candidate = call_cortex(context, classified) or template_ticket_candidate(classified, context)

        # Register + dispatch via Control Plane
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
                candidate["ticketCandidateId"] = tc.get("ticket_candidate_id", candidate["ticketCandidateId"])

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
            jira_result = dispatch.json() if dispatch.status_code < 500 else {"error": dispatch.text}

        report_stage(
            pipeline_run_id,
            "AGENTIC_RAG",
            "COMPLETED",
            artifact_ids=[candidate["ticketCandidateId"]],
            parent_artifact_ids=[classified.get("classifiedId", "")],
            metrics={"ticket_candidates": 1},
        )
        # Mark JIRA stage based on tool result
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
    import json

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
        "anomaly": {"isAnomaly": True, "anomalyScore": 0.91, "anomalyType": "volume_spike",
                     "supportingEvidence": "38 similar in 1h"},
        "cluster": {"clusterId": "clu-checkout-crash"},
        "classifiedAt": "2026-09-18T10:18:40Z",
    }
    print(json.dumps(template_ticket_candidate(cls, build_grounded_context(cls)), indent=2))
