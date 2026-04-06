from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, Preformatted, SimpleDocTemplate, Spacer


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output" / "pdf"
OUTPUT_PATH = OUTPUT_DIR / "adintel-mcp-guide.pdf"


def build_styles():
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="GuideTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            spaceAfter=18,
            textColor=colors.HexColor("#153a5b"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="GuideHeading",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            spaceBefore=10,
            spaceAfter=6,
            textColor=colors.HexColor("#153a5b"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="GuideBody",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            spaceAfter=6,
            alignment=TA_LEFT,
        )
    )
    styles.add(
        ParagraphStyle(
            name="GuideBullet",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=13,
            leftIndent=14,
            firstLineIndent=-8,
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name="GuideCodeLabel",
            parent=styles["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=colors.HexColor("#4a5568"),
            spaceBefore=4,
            spaceAfter=2,
        )
    )
    styles.add(
        ParagraphStyle(
            name="GuideSmall",
            parent=styles["BodyText"],
            fontName="Helvetica-Oblique",
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor("#5f6b7a"),
            spaceAfter=6,
        )
    )
    return styles


def bullet(text: str, styles):
    return Paragraph(f"&bull; {text}", styles["GuideBullet"])


def code_block(text: str):
    return Preformatted(
        text,
        ParagraphStyle(
            "CodeBlock",
            fontName="Courier",
            fontSize=9,
            leading=11.5,
            leftIndent=10,
            rightIndent=10,
            borderPadding=8,
            backColor=colors.HexColor("#f4f7fb"),
            borderColor=colors.HexColor("#d7e1ec"),
            borderWidth=0.6,
            borderRadius=4,
            spaceAfter=8,
        ),
    )


def build_story():
    s = build_styles()
    story = []

    story.append(Paragraph("AdIntel MCP Guide", s["GuideTitle"]))
    story.append(
        Paragraph(
            "AdIntel is a read-only advertiser intelligence MCP backed by a shared PostgreSQL database and populated from Sensor Tower data.",
            s["GuideBody"],
        )
    )
    story.append(Paragraph("Hosted endpoint: https://adintel-delta.vercel.app/", s["GuideBody"]))

    story.append(Paragraph("What AdIntel MCP Can Do", s["GuideHeading"]))
    for item in [
        "List advertisers currently stored in AdIntel",
        "Return the latest Sensor Tower summary for an advertiser",
        "Show collection health, staleness, failures, and alerts",
        "Show recent collection runs and saved metadata",
        "Return historical metric time series",
        "Compare advertisers side by side",
        "Log advertiser requests for future onboarding",
        "Return the canonical SQL schema text",
    ]:
        story.append(bullet(item, s))

    story.append(Paragraph("Install In Claude", s["GuideHeading"]))
    story.append(Paragraph("Claude Desktop config file:", s["GuideBody"]))
    story.append(Paragraph("~/Library/Application Support/Claude/claude_desktop_config.json", s["GuideSmall"]))
    story.append(Paragraph("Recommended configuration", s["GuideCodeLabel"]))
    story.append(
        code_block(
            '{\n'
            '  "mcpServers": {\n'
            '    "adintel": {\n'
            '      "command": "npx",\n'
            '      "args": [\n'
            '        "-y",\n'
            '        "mcp-remote",\n'
            '        "https://adintel-delta.vercel.app/",\n'
            '        "--transport",\n'
            '        "http-only"\n'
            '      ]\n'
            '    }\n'
            '  }\n'
            '}'
        )
    )
    story.append(Paragraph("Then fully restart Claude Desktop.", s["GuideBody"]))
    story.append(Paragraph("Claude Code install command", s["GuideCodeLabel"]))
    story.append(code_block("claude mcp add --transport http adintel https://adintel-delta.vercel.app/"))

    story.append(Paragraph("Install In Codex", s["GuideHeading"]))
    story.append(Paragraph("Add this to ~/.codex/config.toml and restart Codex.", s["GuideBody"]))
    story.append(code_block('[mcp_servers.adintel]\nurl = "https://adintel-delta.vercel.app/"'))

    story.append(Paragraph("Install In Antigravity", s["GuideHeading"]))
    story.append(Paragraph("Native HTTP configuration", s["GuideCodeLabel"]))
    story.append(
        code_block(
            '{\n'
            '  "mcpServers": {\n'
            '    "adintel": {\n'
            '      "serverUrl": "https://adintel-delta.vercel.app/"\n'
            '    }\n'
            '  }\n'
            '}'
        )
    )
    story.append(Paragraph("Or use mcp-remote if needed", s["GuideCodeLabel"]))
    story.append(
        code_block(
            '{\n'
            '  "mcpServers": {\n'
            '    "adintel": {\n'
            '      "command": "npx",\n'
            '      "args": [\n'
            '        "-y",\n'
            '        "mcp-remote",\n'
            '        "https://adintel-delta.vercel.app/"\n'
            '      ]\n'
            '    }\n'
            '  }\n'
            '}'
        )
    )

    story.append(Paragraph("Available Tools", s["GuideHeading"]))
    tools = [
        ("list_advertisers", "Lists advertisers currently stored in AdIntel, including catalog metadata."),
        ("get_advertiser_summary", "Returns the latest Sensor Tower summary for one advertiser, optionally filtered by country."),
        ("request_advertiser", "Logs a missing advertiser request for later onboarding."),
        ("list_requested_advertisers", "Shows advertisers that have been requested but are not yet onboarded."),
        ("read_schema_text", "Returns the canonical SQL schema text for AdIntel."),
        ("get_collection_health", "Shows freshness, last success, failures, and recent errors for one advertiser or all advertisers."),
        ("get_collection_alerts", "Surfaces stale data, repeated failures, and never-succeeded advertisers."),
        ("get_recent_collection_runs", "Returns recent collection runs with timestamps, status, messages, and metadata."),
        ("get_metric_timeseries", "Returns historical daily data for downloads, usage, retention, impression_share, rankings, and reviews."),
        ("compare_advertisers", "Compares the latest values for two or more advertisers side by side."),
    ]
    for name, desc in tools:
        story.append(Paragraph(f"<b>{name}</b>: {desc}", s["GuideBody"]))

    story.append(Paragraph("Recommended Sample Question", s["GuideHeading"]))
    story.append(
        Paragraph(
            "For Binance, Coinbase, and Kraken in the US, compare the latest downloads and usage metrics, tell me which advertiser looks strongest right now, flag any collection health issues or stale data, and summarize the latest Sensor Tower snapshot for each advertiser in a concise table.",
            s["GuideBody"],
        )
    )

    story.append(Paragraph("Appendix A: Data Availability", s["GuideHeading"]))
    for item in [
        "Shared operational tables: advertisers, scrape_runs, scrape_run_metrics, requested_advertisers",
        "Sensor Tower domains: downloads, usage, retention, impression share, demographics, rankings, reviews, review texts, creatives, and ASO keywords",
        "Time series and comparison tools currently support downloads, usage, retention, impression_share, rankings, and reviews",
        "Coverage can vary by advertiser, country, and metric",
    ]:
        story.append(bullet(item, s))

    story.append(Paragraph("Appendix B: Advertiser Availability", s["GuideHeading"]))
    story.append(Paragraph("Current active advertisers in config/advertisers.yaml: 24", s["GuideBody"]))
    advertisers_text = (
        "Chime, Binance, Albert, Coinbase, eToro, Travel Town, Pokemon GO, Royal Match, "
        "MoneyLion, Kraken, Current, Dave, Koho, Mistplay, MonopolyGo, Possible Finance, "
        "Realtor, ScrabbleGo, Shopback, Stash, Tilt, Upside, swagbucks, testerup"
    )
    story.append(Paragraph(advertisers_text, s["GuideBody"]))

    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph("Inactive commented-out catalog entries: Varo, freecash", s["GuideSmall"]))
    return story


def add_page_number(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#5f6b7a"))
    canvas.drawRightString(7.2 * inch, 0.45 * inch, f"Page {doc.page}")
    canvas.restoreState()


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT_PATH),
        pagesize=letter,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.65 * inch,
        title="AdIntel MCP Guide",
        author="Codex",
    )
    doc.build(build_story(), onFirstPage=add_page_number, onLaterPages=add_page_number)
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
