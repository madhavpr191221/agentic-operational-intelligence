"""Normalize investigator search traces for a readable report view."""

from __future__ import annotations

from typing import Any


_SOURCE_LABELS = {
    "customer_tickets": "Customer tickets",
    "support_tickets": "Customer tickets",
    "issues": "Issues",
    "issue_tracker": "Issues",
    "changes": "Changes",
    "change_log": "Changes",
    "operational_docs": "Reference library",
    "knowledge_docs": "Reference library",
    "runbooks": "Runbooks",
}


def summarize_search_trace(trace: Any) -> list[dict[str, Any]]:
    """Build a compact, stable search summary from current or legacy tool traces."""
    if not isinstance(trace, list):
        return []

    summaries: list[dict[str, Any]] = []
    for entry in trace:
        if not isinstance(entry, dict):
            continue
        tool = entry.get("tool")
        if tool not in {"search_evidence", "search_operational_docs", "search_runbooks",
                        "search_related_evidence"}:
            continue
        arguments = entry.get("arguments")
        result = entry.get("result")
        if not isinstance(arguments, dict) or not isinstance(result, dict):
            continue

        sources: list[dict[str, Any]] = []
        documents: list[dict[str, Any]] = []
        unavailable = result.get("unavailable_sources", {})
        if not isinstance(unavailable, dict):
            unavailable = {}

        if tool == "search_evidence":
            source_results = result.get("results", {})
            if not isinstance(source_results, dict):
                source_results = {}
            requested_sources = arguments.get("sources", [])
            if not isinstance(requested_sources, list):
                requested_sources = []
            for source in requested_sources:
                source_result = source_results.get(source)
                records = _records(source_result)
                source_count = _count(source_result, len(records))
                sources.append({
                    "source": source,
                    "label": _SOURCE_LABELS.get(str(source), str(source).replace("_", " ").title()),
                    "count": source_count,
                    "error": str(unavailable[source]) if source in unavailable else None,
                })
                if source == "operational_docs":
                    documents.extend(_document_hit(record) for record in records if _is_document(record))
        else:
            legacy_source = {
                "search_operational_docs": "operational_docs",
                "search_runbooks": "operational_docs",
                "search_related_evidence": "mixed_text_records",
            }[tool]
            records = _records(result)
            sources.append({
                "source": legacy_source,
                "label": "Reference library" if legacy_source == "operational_docs" else "Related records",
                "count": _count(result, len(records)),
                "error": str(result["error"]) if result.get("error") else None,
            })
            documents.extend(_document_hit(record) for record in records if _is_document(record))

        summaries.append({
            "category": arguments.get("category"),
            "query": str(arguments.get("query", "")),
            "available_through": result.get("available_through", arguments.get("as_of")),
            "archive_index_ready": result.get("archive_index_ready"),
            "sources": sources,
            "documents": documents[:5],
        })
    return summaries


def _records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    for records in value.values():
        if isinstance(records, list):
            return [item for item in records if isinstance(item, dict)]
    return []


def _count(value: Any, fallback: int) -> int:
    if isinstance(value, dict) and isinstance(value.get("count"), int):
        return int(value["count"])
    return fallback


def _is_document(record: dict[str, Any]) -> bool:
    return record.get("source") in {"knowledge_docs", "runbooks"}


def _document_hit(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}
    return {
        "evidence_id": record.get("evidence_id"),
        "source": record.get("source"),
        "title": payload.get("title") or payload.get("document_id") or "Reference passage",
        "section_title": payload.get("section_title"),
        "page_number": payload.get("page_number"),
        "snippet": payload.get("text") or record.get("summary") or "",
        "available_at": record.get("available_at"),
        "source_path": payload.get("source_path"),
        "retrieval": record.get("retrieval") if isinstance(record.get("retrieval"), dict) else {},
    }
