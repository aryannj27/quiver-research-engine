"""
score.py — Anomaly detection & editorial scoring.

Design principle: LLMs are bad at anomaly detection and good at narration.
So scoring is 100% deterministic, auditable Python. Claude only sees the
top-N ranked anomalies downstream in narrate.py.

Scoring philosophy ("The Pelosi Principle"):
  A lead's value = name recognizability + conflict signal + dollar magnitude.
  We score each rule on 0-100 and keep ones that clear the editorial bar.

Rules implemented (from highest to lowest signal):
  R1. Congressional options trades (leverage = conviction = newsworthiness)
  R2. Large-dollar congressional stock trades ($100k+)
  R3. Cross-dataset conflict: congress trade + lobbying in same ticker
  R4. Cross-dataset conflict: congress trade + gov contract quarterly spike
  R5. Cluster trading: same ticker traded by 3+ members in same direction
  R6. Dark pool anomaly: unusual DPI on a ticker congress is trading
  R7. Insider dump: executives/directors net-selling >$10M on a ticker congress
      is also active in (schema confirmed against NVDA 4/13 pull)

Each rule returns Lead objects. Leads are then ranked by composite score.
"""
from __future__ import annotations
import json, datetime as dt
from pathlib import Path
from collections import defaultdict, Counter
from dataclasses import dataclass, field, asdict
from typing import Any

DATA_DIR = Path(__file__).parent / "data"

# Editorial thresholds — tuned based on what David's newsletter would actually
# feature. Adjustable in the playbook.
DOLLAR_THRESHOLD_LARGE = 100_000        # congress stock trade
DOLLAR_THRESHOLD_OPTIONS = 15_000       # any options trade (leverage lowers bar)
CLUSTER_MIN_MEMBERS = 3
CLUSTER_WINDOW_DAYS = 14
CROSS_DATASET_WINDOW_DAYS = 90
DARK_POOL_DPI_ALERT = 0.55              # >55% of off-exchange vol is short

# Known high-profile names that auto-boost the "recognizability" score
HIGH_PROFILE = {
    "Nancy Pelosi", "Paul Pelosi", "Josh Gottheimer", "Dan Crenshaw",
    "Ro Khanna", "Michael McCaul", "Tommy Tuberville", "Elizabeth Warren",
    "Sheldon Whitehouse", "Ted Cruz", "Mitch McConnell", "Chuck Schumer",
    "Alexandria Ocasio-Cortez", "Marjorie Taylor Greene", "Byron Donalds",
    "Ron Wyden", "Rand Paul", "Susan Collins", "Bernie Sanders",
}

@dataclass
class Lead:
    rule: str                       # which rule fired
    score: int                      # 0-100 editorial score
    headline: str                   # one-line summary: name + conflict + $$
    name: str                       # the person / entity
    ticker: str
    dollar_amount: str              # human-readable range
    conflict: str                   # the "why this matters" angle
    evidence: list[dict] = field(default_factory=list)  # raw data refs
    tags: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def _parse_range_midpoint(range_str: str) -> float:
    """'$1,001 - $15,000' -> 8000.5. Handles Quiver's disclosure bands."""
    if not range_str:
        return 0.0
    import re
    nums = re.findall(r"[\d,]+", range_str)
    vals = [float(n.replace(",", "")) for n in nums]
    if len(vals) == 2:
        return sum(vals) / 2
    return vals[0] if vals else 0.0


def _format_dollar(amt: float) -> str:
    if amt >= 1_000_000: return f"${amt/1_000_000:.1f}M"
    if amt >= 1_000:     return f"${amt/1_000:.0f}K"
    return f"${amt:.0f}"


def _profile_boost(name: str) -> int:
    return 15 if name in HIGH_PROFILE else 0


def _days_between(d1: str, d2: str) -> int:
    try:
        return abs((dt.date.fromisoformat(d1) - dt.date.fromisoformat(d2)).days)
    except Exception:
        return 999_999


# ---------------------------------------------------------------------------
# RULE IMPLEMENTATIONS
# ---------------------------------------------------------------------------

def rule_options_trades(trades: list[dict]) -> list[Lead]:
    """R1: Options trades by members of Congress. Leverage implies conviction."""
    leads = []
    for t in trades:
        if t.get("TickerType") != "OP":
            continue
        amt = _parse_range_midpoint(t.get("Range", ""))
        if amt < DOLLAR_THRESHOLD_OPTIONS:
            continue
        name = t.get("Representative", "Unknown")
        ticker = t.get("Ticker", "?")
        desc = t.get("Description") or "options position"
        trans = t.get("Transaction", "")
        score = 55 + _profile_boost(name) + min(20, int(amt / 50_000))
        if amt >= 500_000: score += 10
        leads.append(Lead(
            rule="R1_options_trade",
            score=min(score, 100),
            headline=f"{name} ({t.get('Party','?')}-{t.get('House','?')[0]}) bought {ticker} options — {desc}",
            name=name, ticker=ticker,
            dollar_amount=t.get("Range", ""),
            conflict=(f"Options positions are leveraged directional bets. A "
                      f"sitting member of Congress taking a ~{_format_dollar(amt)} "
                      f"position in {ticker} {trans.lower()} — strike/expiry: {desc} — "
                      f"reflects high conviction and warrants scrutiny of committee "
                      f"assignments and pending legislation affecting {ticker}."),
            evidence=[t],
            tags=["options", "congress", name.split()[-1].lower()],
        ))
    return leads


def rule_large_dollar_trades(trades: list[dict]) -> list[Lead]:
    """R2: Stock trades in large dollar bands."""
    leads = []
    for t in trades:
        if t.get("TickerType") == "OP":
            continue  # already covered by R1
        amt = _parse_range_midpoint(t.get("Range", ""))
        if amt < DOLLAR_THRESHOLD_LARGE:
            continue
        name = t.get("Representative", "Unknown")
        ticker = t.get("Ticker", "?")
        trans = t.get("Transaction", "")
        excess = t.get("ExcessReturn") or 0
        score = 40 + _profile_boost(name) + min(25, int(amt / 100_000) * 5)
        if abs(excess) > 10: score += 10  # trade already looks well-timed or badly-timed
        leads.append(Lead(
            rule="R2_large_trade",
            score=min(score, 100),
            headline=f"{name} {trans.lower()} {ticker} — {t.get('Range','')}",
            name=name, ticker=ticker,
            dollar_amount=t.get("Range", ""),
            conflict=(f"A {t.get('Range','')} transaction in {ticker} is large "
                      f"relative to typical congressional disclosures. Post-trade "
                      f"excess return vs SPY: {excess:+.1f}%. Worth checking if "
                      f"{name} sits on committees with jurisdiction over {ticker}'s sector."),
            evidence=[t],
            tags=["large-trade", "congress"],
        ))
    return leads


def rule_cluster_trading(trades: list[dict]) -> list[Lead]:
    """R5: Same ticker traded same direction by >=3 members in a 2-week window."""
    leads = []
    by_ticker_dir = defaultdict(list)  # (ticker, direction) -> [trades]
    for t in trades:
        ticker = t.get("Ticker")
        trans = (t.get("Transaction") or "").lower()
        direction = "buy" if "purchase" in trans else ("sell" if "sale" in trans else None)
        if not ticker or not direction:
            continue
        by_ticker_dir[(ticker, direction)].append(t)

    for (ticker, direction), group in by_ticker_dir.items():
        members = {t["Representative"] for t in group}
        if len(members) < CLUSTER_MIN_MEMBERS:
            continue
        # Check window
        dates = sorted(t.get("TransactionDate", "") for t in group)
        if _days_between(dates[0], dates[-1]) > CLUSTER_WINDOW_DAYS:
            continue
        parties = Counter(t.get("Party") for t in group)
        bipartisan = len(parties) > 1
        total_dollars = sum(_parse_range_midpoint(t.get("Range", "")) for t in group)
        score = 50 + len(members) * 5 + (15 if bipartisan else 0)
        score += sum(_profile_boost(m) for m in members) // 2
        leads.append(Lead(
            rule="R5_cluster",
            score=min(score, 100),
            headline=f"Cluster: {len(members)} members {'BOUGHT' if direction=='buy' else 'SOLD'} {ticker} in {_days_between(dates[0], dates[-1])} days",
            name=", ".join(sorted(members)[:4]) + ("..." if len(members) > 4 else ""),
            ticker=ticker,
            dollar_amount=f"~{_format_dollar(total_dollars)} total",
            conflict=(f"{len(members)} members of Congress "
                      f"({'bipartisan' if bipartisan else 'single-party'}) "
                      f"{'bought' if direction=='buy' else 'sold'} {ticker} "
                      f"within {_days_between(dates[0], dates[-1])} days. "
                      f"Cluster activity of this size often precedes or follows "
                      f"non-public information flow — committee briefings, "
                      f"pending regulation, or sector-wide macro events."),
            evidence=group,
            tags=["cluster", "bipartisan" if bipartisan else "partisan"],
        ))
    return leads


def rule_cross_dataset_lobbying(trades: list[dict], enrichment: dict) -> list[Lead]:
    """R3: Congress trade + lobbying activity in the same ticker (90d window)."""
    leads = []
    for ticker, enr in enrichment.items():
        lobbying = enr.get("lobbying") or []
        if not lobbying:
            continue
        ticker_trades = [t for t in trades if t.get("Ticker") == ticker]
        if not ticker_trades:
            continue
        # Find lobbying records with a date close to any congress trade
        for tr in ticker_trades:
            td = tr.get("TransactionDate", "")
            if not td: continue
            recent_lobby = []
            for lb in lobbying:
                ld = lb.get("Date") or lb.get("Filing_Date") or ""
                if ld and _days_between(td, ld) <= CROSS_DATASET_WINDOW_DAYS:
                    recent_lobby.append(lb)
            if not recent_lobby:
                continue
            total_lobby = sum(float(lb.get("Amount", 0) or 0) for lb in recent_lobby)
            name = tr.get("Representative", "Unknown")
            score = 70 + _profile_boost(name)
            if total_lobby > 500_000: score += 10
            leads.append(Lead(
                rule="R3_cross_lobbying",
                score=min(score, 100),
                headline=f"{name} traded {ticker} near {_format_dollar(total_lobby)} of {ticker} lobbying activity",
                name=name, ticker=ticker,
                dollar_amount=tr.get("Range", ""),
                conflict=(f"{name} {tr.get('Transaction','').lower()} {ticker} on "
                          f"{td} — within {CROSS_DATASET_WINDOW_DAYS} days of "
                          f"{_format_dollar(total_lobby)} in {ticker} federal lobbying "
                          f"spend. Cross-dataset signal: possible conflict between "
                          f"member's committee work and personal trades."),
                evidence=[tr] + recent_lobby[:3],
                tags=["cross-dataset", "lobbying", "conflict-of-interest"],
            ))
            break  # one lead per (ticker, member), not per trade
    return leads


def rule_cross_dataset_contracts(trades: list[dict], enrichment: dict) -> list[Lead]:
    """R4: Congress trade + gov contract activity for same ticker.

    Quiver's /historical/govcontracts/{ticker} returns quarterly rollups:
      [{Ticker, Amount, Qtr, Year}, ...]
    So we compare the trade's quarter-of-year against the most recent
    quarterly spike in contract awards for that ticker.
    """
    leads = []
    for ticker, enr in enrichment.items():
        contracts = enr.get("contracts") or []
        if not contracts or len(contracts) < 4:
            continue
        ticker_trades = [t for t in trades if t.get("Ticker") == ticker]
        if not ticker_trades:
            continue

        # Compute recent quarterly average and detect spike
        amounts = [float(c.get("Amount", 0) or 0) for c in contracts[:8]]  # last 2 yrs
        if len(amounts) < 4: continue
        recent_q = amounts[0]
        trailing_avg = sum(amounts[1:5]) / 4 if len(amounts) >= 5 else recent_q
        if trailing_avg == 0: continue
        spike_ratio = recent_q / trailing_avg

        # Only flag if the most recent quarter is >=1.5x the 4-qtr trailing avg
        if spike_ratio < 1.5 or recent_q < 1_000_000:
            continue

        latest = contracts[0]
        for tr in ticker_trades:
            td = tr.get("TransactionDate", "")
            if not td: continue
            # Was the trade in the same quarter or one before/after the spike?
            try:
                trade_year = int(td[:4])
                trade_month = int(td[5:7])
                trade_qtr = (trade_month - 1) // 3 + 1
                if abs((trade_year * 4 + trade_qtr) - (latest["Year"] * 4 + latest["Qtr"])) > 1:
                    continue
            except Exception:
                continue

            name = tr.get("Representative", "Unknown")
            score = 75 + _profile_boost(name)
            if recent_q > 10_000_000_000: score += 10
            leads.append(Lead(
                rule="R4_cross_contracts",
                score=min(score, 100),
                headline=f"{name} traded {ticker} during {spike_ratio:.1f}x federal-contract quarterly spike",
                name=name, ticker=ticker,
                dollar_amount=tr.get("Range", ""),
                conflict=(f"{name} {tr.get('Transaction','').lower()} {ticker} on "
                          f"{td}. In Q{latest['Qtr']} {latest['Year']}, {ticker} received "
                          f"{_format_dollar(recent_q)} in federal contracts — "
                          f"{spike_ratio:.1f}× its trailing 4-quarter average. "
                          f"Strong 'name + conflict + dollar' triple for the newsletter."),
                evidence=[tr] + contracts[:3],
                tags=["cross-dataset", "contracts", "conflict-of-interest"],
            ))
            break
    return leads


def rule_insider_dump(trades: list[dict], enrichment: dict) -> list[Lead]:
    """R7: Executives/directors net-selling heavily on a ticker congress is also active in.

    Schema (from /beta/live/insiders?ticker=X):
      Name, TransactionCode (S=sell, P=buy), Shares, PricePerShare,
      isOfficer, isDirector, isTenPercentOwner, fileDate
    """
    leads = []
    for ticker, enr in enrichment.items():
        insiders = enr.get("insiders") or []
        if not insiders or len(insiders) < 3:
            continue
        ticker_trades = [t for t in trades if t.get("Ticker") == ticker]
        if not ticker_trades:
            continue

        # Sum net insider $ flow over last 30 days
        cutoff = (dt.date.today() - dt.timedelta(days=30)).isoformat()
        net_flow = 0.0
        sellers = set()
        for ins in insiders:
            d = (ins.get("Date") or "")[:10]
            if d < cutoff: continue
            code = ins.get("TransactionCode", "")
            shares = float(ins.get("Shares", 0) or 0)
            price = float(ins.get("PricePerShare", 0) or 0)
            value = shares * price
            if code == "S":
                net_flow -= value
                sellers.add(ins.get("Name", "?"))
            elif code == "P":
                net_flow += value

        if net_flow > -10_000_000:  # need >=$10M net selling to flag
            continue

        tr = max(ticker_trades, key=lambda x: _parse_range_midpoint(x.get("Range", "")))
        name = tr.get("Representative", "Unknown")
        score = 60 + min(20, int(abs(net_flow) / 10_000_000)) + _profile_boost(name)
        leads.append(Lead(
            rule="R7_insider_dump",
            score=min(score, 100),
            headline=f"{ticker}: {len(sellers)} insiders dumped {_format_dollar(abs(net_flow))} in 30d — congress also active",
            name=name, ticker=ticker,
            dollar_amount=tr.get("Range", ""),
            conflict=(f"Corporate insiders at {ticker} net-sold {_format_dollar(abs(net_flow))} "
                      f"in the last 30 days across {len(sellers)} distinct sellers. "
                      f"Meanwhile {name} {tr.get('Transaction','').lower()} {ticker} on "
                      f"{tr.get('TransactionDate','')}. When insiders and Congress move "
                      f"the same direction, the signal is worth a verify pass."),
            evidence=[tr] + insiders[:3],
            tags=["insider-dump", "confluence"],
        ))
    return leads


def rule_darkpool_on_congress_tickers(trades: list[dict], darkpool: list[dict]) -> list[Lead]:
    """R6: Congress trading a ticker that also has elevated dark-pool short %."""
    leads = []
    congress_tickers = {t["Ticker"] for t in trades if t.get("Ticker")}
    for d in darkpool:
        ticker = d.get("Ticker")
        dpi = d.get("DPI", 0) or 0
        if ticker not in congress_tickers or dpi < DARK_POOL_DPI_ALERT:
            continue
        ticker_trades = [t for t in trades if t.get("Ticker") == ticker]
        if not ticker_trades:
            continue
        # Pick the most notable congress trade for this ticker
        tr = max(ticker_trades, key=lambda x: _parse_range_midpoint(x.get("Range", "")))
        name = tr.get("Representative", "Unknown")
        score = 45 + int((dpi - 0.5) * 100) + _profile_boost(name)
        leads.append(Lead(
            rule="R6_darkpool_confluence",
            score=min(score, 100),
            headline=f"{ticker}: {dpi*100:.0f}% dark-pool short rate + {name} congressional trade",
            name=name, ticker=ticker,
            dollar_amount=tr.get("Range", ""),
            conflict=(f"{ticker} shows elevated off-exchange short activity "
                      f"(DPI {dpi:.2f}, total OTC volume {d.get('OTC_Total', 0):,}) "
                      f"on {d.get('Date')} — same period {name} "
                      f"{tr.get('Transaction','').lower()}. Dark-pool shorting "
                      f"plus insider/congressional activity is worth a verify pass."),
            evidence=[tr, d],
            tags=["dark-pool", "confluence"],
        ))
    return leads


# ---------------------------------------------------------------------------
# ORCHESTRATION
# ---------------------------------------------------------------------------

def score_all(congress: list[dict], darkpool: list[dict], enrichment: dict) -> list[Lead]:
    all_leads: list[Lead] = []
    print("[score] R1 options trades...")
    all_leads += rule_options_trades(congress)
    print("[score] R2 large-dollar trades...")
    all_leads += rule_large_dollar_trades(congress)
    print("[score] R3 cross-dataset lobbying...")
    all_leads += rule_cross_dataset_lobbying(congress, enrichment)
    print("[score] R4 cross-dataset contracts...")
    all_leads += rule_cross_dataset_contracts(congress, enrichment)
    print("[score] R5 cluster trading...")
    all_leads += rule_cluster_trading(congress)
    print("[score] R6 dark-pool confluence...")
    all_leads += rule_darkpool_on_congress_tickers(congress, darkpool)
    print("[score] R7 insider dump confluence...")
    all_leads += rule_insider_dump(congress, enrichment)

    # Dedupe: if same (name, ticker) appears in multiple rules, keep highest-scoring
    seen = {}
    for lead in all_leads:
        key = (lead.name, lead.ticker, lead.rule)
        if key not in seen or lead.score > seen[key].score:
            seen[key] = lead
    deduped = sorted(seen.values(), key=lambda l: -l.score)
    print(f"[score] {len(all_leads)} raw leads -> {len(deduped)} after dedupe")
    return deduped


def load_latest() -> tuple[list, list, dict]:
    def _load(name, default):
        p = DATA_DIR / f"{name}_latest.json"
        if not p.exists(): return default
        return json.loads(p.read_text())
    return _load("congress", []), _load("darkpool", []), _load("enrichment", {})


def main():
    congress, darkpool, enrichment = load_latest()
    if not congress:
        print("ERROR: no congress data. Run pull.py first.")
        return []
    leads = score_all(congress, darkpool, enrichment)
    # Write leads for narrate.py
    out = DATA_DIR / "leads_latest.json"
    out.write_text(json.dumps([l.to_dict() for l in leads], indent=2, default=str))
    print(f"[score] wrote {len(leads)} leads -> {out}")
    # Print top 10 preview
    print("\n--- TOP 10 LEADS PREVIEW ---")
    for i, l in enumerate(leads[:10], 1):
        print(f"{i:2d}. [{l.score}] {l.rule}: {l.headline}")
    return leads

if __name__ == "__main__":
    main()
