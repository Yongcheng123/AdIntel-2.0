"""
Infer sentiment for otterlyai_prompts rows where sentiment_label IS NULL.

Uses Claude to score each prompt text against the target brand:
  positive  — the prompt context favors or benefits the brand
  negative  — the prompt context disfavors or challenges the brand
  neutral   — informational, comparison, or no clear lean

Usage:
    python scripts/infer_geo_sentiment.py            # dry-run (prints but does not write)
    python scripts/infer_geo_sentiment.py --write    # writes labels back to DB
    python scripts/infer_geo_sentiment.py --write --batch-size 50
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import typer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from adintel.core.settings import get_settings
from adintel.db.session import build_session_factory
from adintel.db.models import OtterlyPromptRecord
from sqlalchemy import select

app = typer.Typer(add_completion=False)

SYSTEM_PROMPT = """\
You are a GEO (Generative Engine Optimization) analyst scoring search prompts.

Given a search prompt and a target brand/domain, classify the prompt's sentiment
toward that brand from the perspective of an AI search engine responding to it:

- positive: the prompt is likely to lead to a favorable mention or recommendation
  of the brand (e.g. "best apps like X", "how to use X", brand-named positively)
- negative: the prompt is likely to lead to criticism, comparison unfavorable to
  the brand, or a pain-point context (e.g. "X alternatives", "X complaints",
  "why is X bad")
- neutral: informational, generic category questions, or balanced comparisons
  where neither positive nor negative lean is clear

Respond with ONLY a JSON object with two keys:
  "label": one of "positive", "negative", "neutral"
  "reason": one short sentence explaining why
"""


def classify_batch(prompts: list[dict], client) -> list[dict]:
    """Send one Claude call per prompt (batch sequentially to stay within rate limits)."""
    results = []
    for item in prompts:
        prompt_text = item["prompt_text"]
        brand = item["target_brand_or_domain_name"]
        user_msg = f'Brand: {brand}\nPrompt: "{prompt_text}"'
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=128,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = response.content[0].text.strip()
            parsed = json.loads(raw)
            label = parsed.get("label", "neutral")
            if label not in {"positive", "negative", "neutral"}:
                label = "neutral"
            results.append({"id": item["id"], "label": label, "reason": parsed.get("reason", "")})
        except Exception as exc:
            typer.echo(f"  [warn] id={item['id']} failed: {exc}", err=True)
            results.append({"id": item["id"], "label": "neutral", "reason": f"inference error: {exc}"})
    return results


@app.command()
def main(
    write: bool = typer.Option(False, "--write", help="Write inferred labels back to DB."),
    batch_size: int = typer.Option(25, "--batch-size", help="Rows per Claude call batch."),
    delay: float = typer.Option(0.3, "--delay", help="Seconds between API calls."),
    brand_filter: str | None = typer.Option(None, "--brand", help="Limit to one brand domain."),
) -> None:
    try:
        import anthropic
    except ImportError:
        typer.echo("Install anthropic SDK: pip install anthropic", err=True)
        raise typer.Exit(1)

    client = anthropic.Anthropic()

    settings = get_settings()
    session_factory = build_session_factory(settings)

    with session_factory() as session:
        q = select(
            OtterlyPromptRecord.id,
            OtterlyPromptRecord.prompt_text,
            OtterlyPromptRecord.target_brand_or_domain_name,
        ).where(OtterlyPromptRecord.sentiment_label.is_(None))
        if brand_filter:
            q = q.where(OtterlyPromptRecord.target_brand_or_domain_name == brand_filter)

        rows = session.execute(q).all()

    if not rows:
        typer.echo("No rows with NULL sentiment_label found.")
        return

    typer.echo(f"Found {len(rows)} rows to classify (write={write}).")

    all_results: list[dict] = []
    for i in range(0, len(rows), batch_size):
        chunk = [{"id": r.id, "prompt_text": r.prompt_text, "target_brand_or_domain_name": r.target_brand_or_domain_name} for r in rows[i : i + batch_size]]
        typer.echo(f"  Classifying rows {i+1}–{i+len(chunk)} …")
        results = classify_batch(chunk, client)
        all_results.extend(results)
        if delay > 0 and i + batch_size < len(rows):
            time.sleep(delay)

    # Print summary
    from collections import Counter
    label_counts = Counter(r["label"] for r in all_results)
    typer.echo(f"\nResults: {dict(label_counts)}")

    if not write:
        typer.echo("\nDry run — pass --write to update the database.")
        for r in all_results[:10]:
            typer.echo(f"  id={r['id']} → {r['label']}  ({r['reason'][:80]})")
        return

    # Write back
    with session_factory() as session:
        for r in all_results:
            session.execute(
                OtterlyPromptRecord.__table__.update()
                .where(OtterlyPromptRecord.id == r["id"])
                .values(sentiment_label=r["label"])
            )
        session.commit()
    typer.echo(f"Updated {len(all_results)} rows in otterlyai_prompts.")


if __name__ == "__main__":
    app()
