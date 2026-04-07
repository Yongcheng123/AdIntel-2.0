from __future__ import annotations

from typing import Any


ENGINE_LABELS = {
    "chatgpt": "ChatGPT",
    "perplexity": "Perplexity",
    "google_ai_overview": "Google AI Overview",
    "google_ai_mode": "Google AI Mode",
    "copilot": "Microsoft Copilot",
    "microsoft_copilot": "Microsoft Copilot",
    "gemini": "Google Gemini",
}


def compact_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in value.items() if v is not None and v != ""}


def normalize_engine_label(service: str | None) -> str | None:
    if not service:
        return None
    return ENGINE_LABELS.get(service.lower(), service)


def normalize_sentiment_label(score: int | float | None) -> str | None:
    if score is None:
        return None
    if score > 10:
        return "positive"
    if score < -10:
        return "negative"
    return "neutral"


def select_target_sentiment(prompt_entry: dict[str, Any], target_brand: str) -> tuple[int | None, str | None]:
    sentiments = prompt_entry.get("sentiments")
    if isinstance(sentiments, list):
        for item in sentiments:
            if not isinstance(item, dict):
                continue
            if str(item.get("brandName", "")).strip().lower() == target_brand.strip().lower():
                score = item.get("nss")
                if isinstance(score, (int, float)):
                    return score, normalize_sentiment_label(score)

    score = None
    if isinstance(prompt_entry.get("sentiment"), dict):
        raw = prompt_entry["sentiment"].get("nss")
        if isinstance(raw, (int, float)):
            score = raw
    return score, normalize_sentiment_label(score)


def extract_competitors(prompt_entry: dict[str, Any]) -> list[str]:
    competitors = []
    for item in prompt_entry.get("competitors", []) or []:
        if not isinstance(item, dict):
            continue
        mentions = item.get("brandMentions", 0) or 0
        domain_mentions = item.get("domainMentions", 0) or 0
        brand_name = item.get("brandName")
        if isinstance(brand_name, str) and brand_name and (mentions > 0 or domain_mentions > 0):
            competitors.append(brand_name)
    return competitors


def extract_citation_competitors(citation_entry: dict[str, Any]) -> list[str]:
    competitors = []
    for item in citation_entry.get("competitors", []) or []:
        if not isinstance(item, dict):
            continue
        brand_name = item.get("brandName")
        if isinstance(brand_name, str) and brand_name:
            competitors.append(brand_name)
    return competitors


def refine_prompt_rows(
    report_payload: dict[str, Any],
    prompts_payload: dict[str, Any],
    *,
    country: str,
    start_date: str,
    end_date: str,
    service: str | None,
) -> list[dict[str, Any]]:
    report_brand = report_payload.get("brand")
    report_domain = report_payload.get("brandDomain")
    target_name = report_domain or report_brand
    target_brand = str(report_brand or target_name or "")

    prompts = prompts_payload.get("prompts") or {}
    search_volumes = prompts.get("searchVolumes") or []
    rows: list[dict[str, Any]] = []

    for prompt_entry in search_volumes:
        if not isinstance(prompt_entry, dict):
            continue

        sentiment_score, sentiment_label = select_target_sentiment(prompt_entry, target_brand)
        row = compact_dict(
            {
                "target_brand_or_domain_name": target_name,
                "country_code": prompt_entry.get("country") or country.lower(),
                "query_window_start_date": start_date[:10],
                "query_window_end_date": end_date[:10],
                "prompt_text": prompt_entry.get("prompt"),
                "prompt_volume": prompt_entry.get("volume"),
                "target_rank": prompt_entry.get("rank"),
                "ai_engine": normalize_engine_label(service),
                "domain_cited": (prompt_entry.get("domainMentions") or 0) > 0,
                "sentiment_score": sentiment_score,
                "sentiment_label": sentiment_label,
                "competitors": extract_competitors(prompt_entry),
            }
        )
        rows.append(row)

    return rows


def refine_citation_rows(
    report_payload: dict[str, Any],
    citations_payload: dict[str, Any],
    *,
    country: str,
    start_date: str,
    end_date: str,
    service: str | None,
) -> list[dict[str, Any]]:
    target_name = report_payload.get("brandDomain") or report_payload.get("brand")
    cited_urls = citations_payload.get("citedUrls") or []
    rows: list[dict[str, Any]] = []

    for entry in cited_urls:
        if not isinstance(entry, dict):
            continue
        row = compact_dict(
            {
                "target_brand_or_domain_name": target_name,
                "country_code": country.lower(),
                "query_window_start_date": start_date[:10],
                "query_window_end_date": end_date[:10],
                "ai_engine": normalize_engine_label(service),
                "cited_url": entry.get("url"),
                "cited_domain": entry.get("domain"),
                "citation_count": entry.get("citations"),
                "brand_mentioned": bool(entry.get("brandMentioned")),
                "domain_category": entry.get("domainCategory"),
                "competitors": extract_citation_competitors(entry),
            }
        )
        rows.append(row)

    return rows
