"""
brief.py — Sunday research brief HTML generator.

Output: a single self-contained HTML file (output/sunday_brief_YYYY-MM-DD.html)
that David opens Monday morning. Designed to be forwarded or pasted into
editorial workflow directly. No external dependencies at render time.
"""
from __future__ import annotations
import json, datetime as dt, html
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
OUT_DIR = Path(__file__).parent / "output"
OUT_DIR.mkdir(exist_ok=True)

RULE_LABELS = {
    "R1_options_trade":       ("Options Leverage",      "#FF1D58"),
    "R2_large_trade":         ("Large-Dollar Trade",    "#007AFF"),
    "R3_cross_lobbying":      ("Trade ×  Lobbying",     "#AF52DE"),
    "R4_cross_contracts":     ("Trade ×  Gov Contract", "#34C759"),
    "R5_cluster":             ("Member Cluster",        "#FF9500"),
    "R6_darkpool_confluence": ("Dark-Pool Confluence",  "#5856D6"),
    "R7_insider_dump":        ("Insider × Congress",    "#FF3B30"),
}

def _e(x) -> str:
    return html.escape(str(x)) if x is not None else ""

def _render_lead_card(i: int, lead: dict) -> str:
    rule_label, rule_color = RULE_LABELS.get(lead["rule"], (lead["rule"], "#8E8E93"))
    narr = lead.get("narration") or {}
    headlines = narr.get("headline", [])
    verify = narr.get("verify", [])
    confidence = narr.get("confidence", "—")
    conf_color = {"high": "#34C759", "medium": "#FF9500", "low": "#8E8E93"}.get(confidence, "#8E8E93")

    evidence_rows = ""
    for ev in lead["evidence"][:3]:
        pretty = " · ".join(
            f"{k}: {_e(v)}" for k, v in ev.items()
            if k in ("Representative","Ticker","Transaction","Range","TransactionDate",
                     "Description","Amount","Date","OTC_Total","DPI")
            and v is not None
        )
        if pretty:
            evidence_rows += f'<div class="evidence-row">{pretty}</div>'

    headline_html = ""
    if headlines:
        headline_html = '<div class="headlines"><div class="mini-label">Draft Headlines</div>' + \
                       "".join(f'<div class="headline-opt">• {_e(h)}</div>' for h in headlines) + \
                       "</div>"

    verify_html = ""
    if verify:
        verify_html = '<div class="verify"><div class="mini-label">Verify Before Publishing</div><ul>' + \
                     "".join(f"<li>{_e(v)}</li>" for v in verify) + "</ul></div>"

    pitch = narr.get("pitch") or lead["headline"]
    angle = narr.get("angle") or lead["conflict"]

    return f"""
    <div class="lead-card">
      <div class="lead-header">
        <div class="lead-rank">#{i}</div>
        <div class="lead-meta">
          <span class="rule-chip" style="background:{rule_color}15;color:{rule_color};border-color:{rule_color}40">{rule_label}</span>
          <span class="score-chip">Score {lead['score']}</span>
          <span class="conf-chip" style="color:{conf_color}">● {confidence.upper()}</span>
        </div>
      </div>
      <div class="lead-pitch">{_e(pitch)}</div>
      <div class="lead-meta-line">
        <strong>{_e(lead['name'])}</strong> · ${_e(lead['ticker'])} · {_e(lead['dollar_amount'])}
      </div>
      <div class="lead-angle">{_e(angle)}</div>
      {headline_html}
      {verify_html}
      <details class="evidence">
        <summary>Raw data ({len(lead['evidence'])} record{'s' if len(lead['evidence'])!=1 else ''})</summary>
        {evidence_rows}
      </details>
    </div>
    """

def render(enriched_leads: list[dict], run_meta: dict) -> str:
    today = dt.date.today().isoformat()
    n_leads = len(enriched_leads)
    n_narrated = sum(1 for l in enriched_leads if l.get("narration"))
    rule_counts = {}
    for l in enriched_leads:
        rule_counts[l["rule"]] = rule_counts.get(l["rule"], 0) + 1

    summary_chips = " ".join(
        f'<span class="summary-chip">{RULE_LABELS.get(r,(r,""))[0]}: {c}</span>'
        for r, c in sorted(rule_counts.items(), key=lambda x: -x[1])
    )

    cards = "\n".join(_render_lead_card(i+1, l) for i, l in enumerate(enriched_leads))

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Quiver Sunday Research Brief — {today}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: #f5f5f7; color: #1d1d1f; line-height: 1.55; padding: 24px; }}
.wrap {{ max-width: 900px; margin: 0 auto; }}
.header {{ background: #000; color: #fff; border-radius: 16px; padding: 28px 32px; margin-bottom: 24px;
           background: linear-gradient(135deg, #000 0%, #1a1a2e 100%); position: relative; overflow: hidden; }}
.header::after {{ content: ''; position: absolute; top: -40px; right: -40px; width: 180px; height: 180px;
                  background: radial-gradient(circle, rgba(255,29,88,0.2) 0%, transparent 70%); }}
.eyebrow {{ font-size: 11px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase;
            color: #FF1D58; margin-bottom: 10px; }}
.header h1 {{ font-size: 1.8rem; font-weight: 700; letter-spacing: -0.5px; margin-bottom: 6px; }}
.header .sub {{ color: #c7c7cc; font-size: 0.95rem; }}
.meta-bar {{ display: flex; gap: 12px; margin-top: 18px; flex-wrap: wrap; position: relative; z-index: 1; }}
.meta-cell {{ background: rgba(255,255,255,0.08); padding: 8px 14px; border-radius: 8px; font-size: 0.85rem; }}
.meta-cell strong {{ color: #fff; }}

.summary {{ background: #fff; border-radius: 14px; padding: 20px 24px; margin-bottom: 24px;
            border: 1px solid rgba(0,0,0,0.06); }}
.summary-title {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px;
                  color: #86868b; margin-bottom: 12px; }}
.summary-chip {{ display: inline-block; background: #f5f5f7; padding: 5px 12px; border-radius: 6px;
                 font-size: 0.82rem; font-weight: 500; margin: 0 6px 6px 0; border: 1px solid #e5e5e7; }}

.lead-card {{ background: #fff; border-radius: 14px; padding: 22px 26px; margin-bottom: 14px;
              border: 1px solid rgba(0,0,0,0.06); }}
.lead-header {{ display: flex; align-items: center; gap: 14px; margin-bottom: 12px; }}
.lead-rank {{ font-size: 1.4rem; font-weight: 700; color: #86868b; min-width: 36px; }}
.lead-meta {{ display: flex; gap: 8px; flex-wrap: wrap; }}
.rule-chip, .score-chip, .conf-chip {{
  font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;
  padding: 4px 10px; border-radius: 5px; border: 1px solid transparent; }}
.score-chip {{ background: #f5f5f7; color: #424245; border-color: #e5e5e7; }}
.conf-chip {{ background: transparent; }}
.lead-pitch {{ font-size: 1.08rem; font-weight: 600; color: #1d1d1f; margin-bottom: 8px;
               letter-spacing: -0.2px; }}
.lead-meta-line {{ font-size: 0.86rem; color: #636366; margin-bottom: 12px;
                   padding-bottom: 12px; border-bottom: 1px solid #f0f0f2; }}
.lead-meta-line strong {{ color: #1d1d1f; }}
.lead-angle {{ font-size: 0.92rem; color: #424245; margin-bottom: 14px; }}

.mini-label {{ font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.8px;
               color: #86868b; margin-bottom: 6px; }}
.headlines, .verify {{ background: #fafafb; border-radius: 8px; padding: 12px 14px; margin-bottom: 10px; }}
.headline-opt {{ font-size: 0.9rem; color: #1d1d1f; margin: 3px 0; font-weight: 500; }}
.verify ul {{ margin: 0; padding-left: 22px; font-size: 0.88rem; color: #424245; }}
.verify li {{ margin: 3px 0; }}

.evidence {{ margin-top: 8px; font-size: 0.82rem; }}
.evidence summary {{ cursor: pointer; color: #007AFF; padding: 4px 0; }}
.evidence-row {{ background: #f5f5f7; padding: 8px 12px; margin: 4px 0; border-radius: 6px;
                  font-family: 'SF Mono', Menlo, monospace; font-size: 0.75rem; color: #424245;
                  word-break: break-word; }}

.footer {{ text-align: center; padding: 24px; color: #86868b; font-size: 0.82rem; }}
</style></head>
<body><div class="wrap">
<div class="header">
  <div class="eyebrow">Quiver Research Engine · Sunday Brief</div>
  <h1>The Monday Leads · {today}</h1>
  <div class="sub">Automated research sprint. {n_leads} scored leads, {n_narrated} narrated by Claude.</div>
  <div class="meta-bar">
    <div class="meta-cell">Run: <strong>{_e(run_meta.get('stamp','—'))}</strong></div>
    <div class="meta-cell">Congress trades scanned: <strong>{run_meta.get('n_congress','—')}</strong></div>
    <div class="meta-cell">Dark-pool tickers: <strong>{run_meta.get('n_darkpool','—')}</strong></div>
    <div class="meta-cell">Enriched tickers: <strong>{run_meta.get('n_enriched','—')}</strong></div>
  </div>
</div>
<div class="summary">
  <div class="summary-title">Anomalies Surfaced</div>
  {summary_chips}
</div>
{cards}
<div class="footer">
  Built by the Quiver Research Engine · Deterministic scoring + Claude narration · Re-runnable any Sunday
</div>
</div></body></html>"""

def main():
    leads_path = DATA_DIR / "leads_narrated.json"
    if not leads_path.exists():
        # fallback to un-narrated leads
        leads_path = DATA_DIR / "leads_latest.json"
    if not leads_path.exists():
        print("ERROR: no leads file. Run pipeline first.")
        return
    leads = json.loads(leads_path.read_text())[:10]

    # Try to pull run metadata
    run_meta = {"stamp": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    for name, key in [("congress", "n_congress"), ("darkpool", "n_darkpool"), ("enrichment", "n_enriched")]:
        p = DATA_DIR / f"{name}_latest.json"
        if p.exists():
            try:
                d = json.loads(p.read_text())
                run_meta[key] = len(d)
            except Exception:
                pass

    html_str = render(leads, run_meta)
    out = OUT_DIR / f"sunday_brief_{dt.date.today().isoformat()}.html"
    out.write_text(html_str)
    print(f"[brief] wrote {out}")
    return out

if __name__ == "__main__":
    main()
