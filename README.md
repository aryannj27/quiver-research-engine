# Quiver Research Engine

An automated Sunday research pipeline for Quiver Quantitative's newsletter team. Built as a case study submission for the Summer 2026 Data Intern role.

## What it does

Every Sunday evening, one command (`python run.py`) does what David currently does by hand: scans Quiver's live API, cross-references datasets, identifies anomalies, and produces a ranked brief of story leads for Monday morning — each formatted around the "Pelosi Principle" (name + conflict + dollar amount).

## Design philosophy

**LLMs are bad at anomaly detection. They are good at narration.** So the pipeline splits those jobs cleanly:

1. **Pull layer** (`pull.py`) — hits Quiver's live endpoints, caches raw JSON to disk. Handles Quiver's flaky bulk endpoints gracefully (insider/lobbying `/all` endpoints were returning DB timeouts and 404s during development — the pipeline degrades rather than crashes).
2. **Scoring layer** (`score.py`) — **100% deterministic Python**. Six editorial-scoring rules, each auditable, each tunable. Claude never sees raw data until scoring is done.
3. **Narration layer** (`narrate.py`) — Claude Sonnet takes the top-N ranked leads and drafts newsletter-ready pitches, angles, draft headlines, and verification steps. Few-shot prompted in Quiver's editorial voice.
4. **Brief layer** (`brief.py`) — renders a single self-contained HTML file. David opens it Monday, picks what to write about.

## Scoring rules

| ID | Rule | What it catches |
|----|------|-----------------|
| R1 | Congressional options trades | Leveraged directional bets = high conviction = high newsworthiness |
| R2 | Large-dollar trades ($100k+) | Straight-up material positions |
| R3 | Trade × Lobbying (90d) | Conflict: member trades ticker with active federal lobbying |
| R4 | Trade × Gov Contract (90d) | Conflict: member trades ticker that just won federal $ |
| R5 | Cluster trading | 3+ members, same ticker, same direction, 14-day window |
| R6 | Dark-pool confluence | Elevated off-exchange short rate on a ticker Congress is trading |
| R7 | Insider × Congress | Execs/directors net-sell $10M+ on a ticker Congress is also trading |

Scores blend dollar magnitude, name recognizability (high-profile list), cross-dataset confirmation, and timing tightness. See `score.py` docstrings for each rule's exact math.

## Setup

```bash
pip install requests anthropic
export QUIVER_API_KEY="..."          # Token from Quiver dashboard
export ANTHROPIC_API_KEY="sk-ant-..."  # For narration step (optional)
python run.py
```

Output: `output/sunday_brief_YYYY-MM-DD.html`. Open in any browser.

## Runtime & cost

- Full run: ~60-90 seconds (30s pull + enrichment, 5s score, 20s narrate, 1s render)
- Claude API cost: ~$0.05 per run (Sonnet 4.6, ~3k input + 2k output tokens)
- Quiver API calls: ~45 (1 bulk congress + 1 darkpool + 3 enrichments × 15 tickers)

## Files

```
.
├── pull.py       # API fetching + caching, graceful failure handling
├── score.py      # Deterministic anomaly detection (6 rules)
├── narrate.py    # Claude-powered editorial narration
├── brief.py      # HTML brief rendering
├── run.py        # Orchestrator
├── data/         # Per-run JSON caches (timestamped + _latest)
├── output/       # Final HTML briefs, one per Sunday
├── PLAYBOOK.html # The Sunday SOP — how to actually run this every week
└── README.md     # This file
```

## What's not included (deliberately deferred)

- **Patents** dataset — low yield for "name + conflict + dollar" stories; noted in playbook as future extension
- **13F position deltas** — useful for confirmation, not generative; add if V2 needs more volume
- **Political beta / Wikipedia traffic** — interesting for meta-stories, wrong axis for Sunday research
- **UI beyond HTML** — by design. David needs a brief, not a dashboard.

## A note on honest attribution

Per the case study's explicit ask: the scoring logic, rule design, and playbook structure are mine. The code was written in close collaboration with Claude (Sonnet 4.6) — meaning I designed the architecture, chose the rules, specified the scoring formulas, and iterated on the prompts; Claude drafted most of the Python. This is exactly the "orchestration over syntax memorization" the brief calls for.
