"""Report generation engine — HTML/SVG/Markdown rendering.

Usage:
    from nous.reports.renderer import ReportRenderer
    renderer = ReportRenderer()
    html = renderer.render_daily_review(data)
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


class ReportRenderer:
    """Renders structured data into HTML reports with CSS styling."""

    STYLE_DARK = """
    body { background:#111; color:#ccc; font-family: -apple-system, sans-serif; margin:2rem; }
    .card { background:#1a1a2e; border-radius:8px; padding:1.5rem; margin:1rem 0; }
    h1,h2,h3 { color:#e0e0e0; }
    .green { color:#22c55e; } .red { color:#ef4444; }
    table { width:100%; border-collapse:collapse; }
    th,td { padding:8px 12px; text-align:left; border-bottom:1px solid #333; }
    .badge { padding:2px 8px; border-radius:4px; font-size:0.85em; }
    .badge-buy { background:#22c55e33; color:#22c55e; }
    .badge-sell { background:#ef444433; color:#ef4444; }
    """

    def __init__(self, theme: str = "dark"):
        self.theme = theme
        self.style = self.STYLE_DARK

    def render_card(self, title: str, content: str) -> str:
        return f'<div class="card"><h3>{title}</h3>{content}</div>'

    def render_table(self, headers: list[str], rows: list[list[str]]) -> str:
        thead = "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
        tbody = "".join(
            "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
            for row in rows
        )
        return f"<table><thead>{thead}</thead><tbody>{tbody}</tbody></table>"

    def render_html(self, title: str, body: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>{title}</title><style>{self.style}</style></head>
<body><h1>{title}</h1>{body}<footer><p>Generated: {datetime.now().isoformat()}</p></footer></body>
</html>"""

    def render_daily_review(self, data: dict) -> str:
        """Render daily market review report."""
        parts = []
        if "summary" in data:
            parts.append(self.render_card("📊 盘面概要", f"<p>{data['summary']}</p>"))
        if "signals" in data:
            parts.append(self.render_card("🎯 信号", "<ul>" + "".join(f"<li>{s}</li>" for s in data["signals"]) + "</ul>"))
        body = "\n".join(parts)
        return self.render_html(f"每日复盘 — {data.get('date', date.today())}", body)

    def render_recommendation(self, data: dict) -> str:
        """Render stock recommendation report."""
        parts = []
        if "stocks" in data:
            headers = ["代码", "名称", "引擎", "得分", "信号"]
            rows = [
                [s.get("symbol", ""), s.get("name", ""), s.get("engine", ""),
                 f"{s.get('score', 0):.1f}", s.get("signal", "")]
                for s in data["stocks"]
            ]
            parts.append(self.render_card("📈 荐股池", self.render_table(headers, rows)))
        body = "\n".join(parts)
        return self.render_html(f"每日荐股 — {data.get('date', date.today())}", body)


# Module-level convenience
renderer = ReportRenderer()
