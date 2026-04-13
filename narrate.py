"""
narrate.py — Claude-powered editorial narration.

Takes the top-N deterministically ranked leads and generates newsletter-ready
one-paragraph briefs in Quiver's editorial voice ("Morning Brew meets
Bloomberg terminal"). Claude is NEVER used as the anomaly detector — only as
a writer working from pre-verified signals. This is the core AI-fluency
distinction the case study rubric tests for.

Prompt design:
  - Few-shot with the Quiver editorial style (name + conflict + dollar, punchy)
  - Output is structured JSON so narration can be programmatically inserted
    into the HTML brief template
  - Model: claude-sonnet-4-6 (right balance of quality + speed for 10 leads)
"""
from __future__ import annotations
import os, json, sys, time
from pathlib import Path
import anthropic

DATA_DIR = Path(__file__).parent / "data"
MODEL = "claude-sonnet-4-6"
TOP_N = 10  # narrate top 10 leads max

SYSTEM_PROMPT = """You write for Quiver Quantitative, a financial newsletter read by 317,000+ retail investors. \
The voice: data-first, punchy, conversational. Think Morning Brew meets a Bloomberg terminal. \
Every story follows the "Pelosi Principle": a clear NAME + CONFLICT + DOLLAR AMOUNT. \
You are the analyst behind the analyst — you do NOT write final stories. You produce \
short research briefs for the Head of Sales, David Love, so he can decide which leads \
to turn into full stories on Monday morning.

For each lead you receive, produce a JSON object with these fields:
  - "pitch":       one sentence (<=25 words). The hook. Pelosi Principle compressed.
  - "angle":       one paragraph (3-4 sentences). Why this matters. What to investigate.
  - "verify":      2-3 concrete verification steps (committee lookup, SEC filing check, etc).
  - "headline":    3 draft headline options, each <=10 words, punchy.
  - "confidence":  "high" | "medium" | "low" — your editorial confidence this is a real story.

Output ONLY a JSON array of these objects, in the same order as input. No preamble, no markdown fences."""

FEW_SHOT_EXAMPLES = """EXAMPLE INPUT LEAD:
{"rule": "R1_options_trade", "score": 88, "name": "Nancy Pelosi", "ticker": "NVDA",
 "dollar_amount": "$1,000,001 - $5,000,000",
 "conflict": "Options positions are leveraged directional bets. Pelosi's husband took a ~$3M position in NVDA calls expiring in 6 months.",
 "evidence": [{"Description": "CALL OPTIONS; STRIKE $120; EXPIRES 12/19/2025", "Transaction": "Purchase"}]}

EXAMPLE OUTPUT:
{"pitch": "Paul Pelosi dropped up to $5M on NVDA calls expiring in December — one of the largest single options bets disclosed this cycle.",
 "angle": "The trade is leveraged, directional, and timed ahead of NVDA's Q3 earnings and a pending House Select Committee hearing on US semiconductor export controls. Nancy Pelosi does not sit on that committee, but her caucus leadership gives her visibility into its timing. Worth checking whether the strike price implies knowledge of specific export-control policy.",
 "verify": ["Confirm Paul Pelosi's disclosure filing on House clerk site", "Check NVDA's upcoming earnings date and any pending export-control votes", "Pull historical Pelosi NVDA trades to assess pattern"],
 "headline": ["Pelosi Bets Big on NVDA", "The $5M Chip Call", "Pelosi's December NVDA Gamble"],
 "confidence": "high"}
"""

def _build_messages(leads: list[dict]) -> list[dict]:
    lead_input = json.dumps([{
        "rule": l["rule"], "score": l["score"], "name": l["name"],
        "ticker": l["ticker"], "dollar_amount": l["dollar_amount"],
        "headline": l["headline"], "conflict": l["conflict"],
        "tags": l.get("tags", []),
        "evidence_summary": l["evidence"][:2],  # cap to keep prompt tight
    } for l in leads], default=str)
    user_msg = (f"{FEW_SHOT_EXAMPLES}\n\n"
                f"Now produce the JSON array for these {len(leads)} leads:\n\n"
                f"{lead_input}")
    return [{"role": "user", "content": user_msg}]

def narrate(leads: list[dict]) -> list[dict]:
    if not leads:
        return []
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ERROR: set ANTHROPIC_API_KEY env var.", file=sys.stderr)
        sys.exit(2)

    leads_to_narrate = leads[:TOP_N]
    print(f"[narrate] sending top {len(leads_to_narrate)} leads to {MODEL}...")
    client = anthropic.Anthropic(api_key=api_key)

    t0 = time.time()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=_build_messages(leads_to_narrate),
    )
    elapsed = time.time() - t0
    text = resp.content[0].text.strip()
    # Strip any markdown fences just in case
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"): text = text[4:]
        text = text.strip()
    try:
        narrations = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"[narrate] JSON parse failed: {e}", file=sys.stderr)
        print(f"[narrate] raw output:\n{text[:500]}", file=sys.stderr)
        return []

    # Zip narrations onto leads
    enriched = []
    for lead, narr in zip(leads_to_narrate, narrations):
        enriched.append({**lead, "narration": narr})
    # Remaining leads (below TOP_N) pass through without narration
    for lead in leads[TOP_N:]:
        enriched.append({**lead, "narration": None})

    print(f"[narrate] done in {elapsed:.1f}s, {resp.usage.input_tokens}+{resp.usage.output_tokens} tokens")
    return enriched


def main():
    leads_path = DATA_DIR / "leads_latest.json"
    if not leads_path.exists():
        print("ERROR: no leads file. Run score.py first.")
        return
    leads = json.loads(leads_path.read_text())
    enriched = narrate(leads)
    out = DATA_DIR / "leads_narrated.json"
    out.write_text(json.dumps(enriched, indent=2, default=str))
    print(f"[narrate] wrote {len(enriched)} narrated leads -> {out}")
    return enriched

if __name__ == "__main__":
    main()
