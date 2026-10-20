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
        return similar
    except Exception as e:
        logger.info("Vector Search retrieve skipped: %s", e)
        return []


def build_grounded_context(classified: dict[str, Any]) -> dict[str, Any]:
    cfg = load_pipeline_config()
    top_k = int(cfg.get("agentic_rag", {}).get("vector_search_top_k", 8))
    text = classified.get("cleanedText") or classified.get("text") or ""
    similar = retrieve_similar_issues(text, top_k=top_k)
    return {
        "feedback_text": text,
        "business_unit": classified.get("businessUnit"),
        "category": classified.get("category"),
        "sub_category": classified.get("subCategory"),
        "severity": classified.get("suggestedSeverity"),
        "confidence": classified.get("confidence"),
        "product": classified.get("product"),
        "channel": classified.get("channel"),
        "cluster": classified.get("cluster"),
        "anomaly": classified.get("anomaly"),
        "similar_issues": similar,
        "prompt_version": cfg.get("agentic_rag", {}).get("prompt_version", "ticket-intel-v4.2"),
    }


def template_ticket_candidate(classified: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    text = context.get("feedback_text") or ""
    summary = (text[:80] + "…") if len(text) > 80 else text or "Customer feedback"
    return {
        "summary": summary,
        "description": text,
        "issue_type": "Bug" if "crash" in text.lower() or "error" in text.lower() else "Task",
        "priority": classified.get("suggestedSeverity") or "Medium",
        "labels": [classified.get("category") or "feedback", classified.get("product") or "general"],
        "components": [classified.get("businessUnit") or "General Support"],
        "deduplication_key": dedup_key(classified),
        "source": "template",
        "grounding": {"similar_count": len(context.get("similar_issues") or [])},
    }


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def call_gemini(context: dict[str, Any], classified: dict[str, Any]) -> dict[str, Any] | None:
    """Call Gemini via Vertex AI GenerativeModel using ADC / Workload Identity."""
    project = os.environ.get("GCP_PROJECT", "intelligent-machines")
    location = os.environ.get("GCP_REGION", "us-central1")
    cfg = load_pipeline_config()
    model_name = (
        os.environ.get("GEMINI_MODEL")
        or cfg.get("agentic_rag", {}).get("gemini_model")
        or "gemini-2.0-flash-001"
    )

    prompt = f"""You are a ticket intelligence agent. Given grounded customer feedback context,
produce a single JSON object for a Jira ticket candidate with keys:
summary, description, issue_type, priority, labels (list), components (list).
Do not invent facts not present in the context. Keep summary under 100 chars.

Context:
{json.dumps(context, indent=2)}
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
                max_output_tokens=1024,
                response_mime_type="application/json",
            ),
        )
        text = response.text if hasattr(response, "text") else str(response)
        data = _extract_json(text)
        if not data:
            return None
        data["deduplication_key"] = dedup_key(classified)
        data["source"] = f"gemini:{model_name}"
        data["grounding"] = {"similar_count": len(context.get("similar_issues") or [])}
        return data
    except Exception as e:
        logger.warning("Gemini call failed (ADC/Vertex): %s", e)
        return None


def run_agentic_rag(pipeline_run_id: str, classified: dict[str, Any]) -> dict[str, Any]:
    report_stage(pipeline_run_id, "AGENTIC_RAG", "RUNNING")
    try:
        context = build_grounded_context(classified)
        candidate = call_gemini(context, classified) or template_ticket_candidate(classified, context)
        candidate["pipeline_run_id"] = pipeline_run_id
        candidate["classified_id"] = classified.get("classifiedId") or classified.get("artifactId")
        candidate["created_at"] = utcnow()

        # Dispatch to Control Plane / JiraTool path (best-effort)
        try:
            with httpx.Client(timeout=30.0) as client:
                client.post(
                    f"{control_plane_url()}/v1/ticket-candidates",
                    json=candidate,
                )
        except Exception as e:
            logger.info("ticket candidate post skipped: %s", e)

        report_stage(
            pipeline_run_id,
            "AGENTIC_RAG",
            "COMPLETED",
            metrics={"ticket_candidates": 1, "source": candidate.get("source", "unknown")},
        )
        return candidate
    except Exception as e:
        report_stage(pipeline_run_id, "AGENTIC_RAG", "FAILED", error=str(e))
        raise


if __name__ == "__main__":
    demo = {
        "classifiedId": "cls-demo",
        "cleanedText": "App crashes every time I try to checkout on Android",
        "businessUnit": "Mobile Engineering",
        "category": "Crash",
        "suggestedSeverity": "High",
        "confidence": 0.91,
        "product": "Checkout",
        "channel": "mobile_app",
        "cluster": {"clusterId": "clu-checkout-crash"},
    }
    print(json.dumps(run_agentic_rag("pipe-demo", demo), indent=2))
