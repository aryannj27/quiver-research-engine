"""
pull.py — Quiver API data pull layer.

Handles:
  - Auth via Bearer token from QUIVER_API_KEY env var
  - Graceful failure: timeouts, 404s, DB errors from Quiver are logged and skipped
    so the rest of the pipeline can run on partial data.
  - Disk caching: every pull is stamped and saved to data/ for reproducibility
    and for the "run it again next Sunday and diff" workflow.

Endpoints targeted (not all guaranteed live — Quiver's backend has flaky
  bulk endpoints as of April 2026):
  - /beta/live/congresstrading        (PRIMARY — signature dataset)
  - /beta/live/offexchange            (SECONDARY — dark pool flow by ticker)
  - /beta/live/insiders?ticker=X      (per-ticker, called for top congress tickers)
  - /beta/historical/lobbying/X       (per-ticker, called for top congress tickers)
  - /beta/historical/govcontracts/X   (per-ticker, called for top congress tickers)
"""
from __future__ import annotations
import os, json, sys, time, datetime as dt
from pathlib import Path
from typing import Any
import requests

BASE = "https://api.quiverquant.com"
TOKEN = os.environ.get("QUIVER_API_KEY", "").strip()
if not TOKEN:
    print("ERROR: set QUIVER_API_KEY env var.", file=sys.stderr)
    sys.exit(2)

HEADERS = {"Authorization": f"Token {TOKEN}"}
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
RUN_STAMP = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")

def _get(path: str, params: dict | None = None, timeout: int = 20) -> Any:
    """GET wrapper. Returns parsed JSON on 200, None on any failure (logged)."""
    url = f"{BASE}/{path.lstrip('/')}"
    try:
        r = requests.get(url, headers=HEADERS, params=params or {}, timeout=timeout)
    except requests.RequestException as e:
        print(f"  [net-fail] {path}: {e}", file=sys.stderr)
        return None
    if r.status_code != 200:
        print(f"  [http {r.status_code}] {path}: {r.text[:120]}", file=sys.stderr)
        return None
    try:
        data = r.json()
    except ValueError:
        print(f"  [bad-json] {path}: {r.text[:120]}", file=sys.stderr)
        return None
    # Quiver sometimes returns a JSON-encoded error string even with 200
    if isinstance(data, str) and ("QueuePool" in data or "timeout" in data.lower()):
        print(f"  [quiver-db-timeout] {path}", file=sys.stderr)
        return None
    return data

def _save(name: str, payload: Any) -> Path:
    path = DATA_DIR / f"{name}_{RUN_STAMP}.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    # Also write a "latest" symlink-style copy so score.py can always find it
    latest = DATA_DIR / f"{name}_latest.json"
    latest.write_text(json.dumps(payload, indent=2, default=str))
    return path

def pull_congress(lookback_days: int = 30) -> list[dict]:
    """Pull live congressional trades, filter to recent."""
    print("[pull] congress trading (live)...")
    data = _get("beta/live/congresstrading")
    if not data:
        return []
    cutoff = (dt.date.today() - dt.timedelta(days=lookback_days)).isoformat()
    recent = [t for t in data if t.get("TransactionDate", "") >= cutoff]
    print(f"  -> {len(data)} total, {len(recent)} within last {lookback_days}d")
    _save("congress", recent)
    return recent

def pull_darkpool() -> list[dict]:
    """Pull latest off-exchange (dark pool) activity."""
    print("[pull] off-exchange (dark pool)...")
    data = _get("beta/live/offexchange")
    if not data:
        return []
    print(f"  -> {len(data)} tickers")
    _save("darkpool", data)
    return data

def pull_insider_for_ticker(ticker: str) -> list[dict]:
    """Per-ticker insider pull. Quiver's live bulk endpoint is unreliable."""
    data = _get("beta/live/insiders", params={"ticker": ticker})
    return data if isinstance(data, list) else []

def pull_lobbying_for_ticker(ticker: str) -> list[dict]:
    data = _get(f"beta/historical/lobbying/{ticker}")
    return data if isinstance(data, list) else []

def pull_contracts_for_ticker(ticker: str) -> list[dict]:
    data = _get(f"beta/historical/govcontracts/{ticker}")
    return data if isinstance(data, list) else []

def enrich_top_tickers(congress_trades: list[dict], top_n: int = 15) -> dict:
    """
    For the most-traded tickers in recent congress activity, pull cross-dataset
    context (insiders, lobbying, contracts). This is the expensive step — we
    deliberately cap at top_n to stay under API limits and keep runtime < 2 min.
    """
    from collections import Counter
    ticker_counts = Counter(t["Ticker"] for t in congress_trades if t.get("Ticker"))
    top_tickers = [tk for tk, _ in ticker_counts.most_common(top_n)]
    print(f"[pull] enriching top {len(top_tickers)} congress tickers: {top_tickers}")

    enrichment = {}
    for i, tk in enumerate(top_tickers, 1):
        print(f"  [{i}/{len(top_tickers)}] {tk}...")
        enrichment[tk] = {
            "insiders":  pull_insider_for_ticker(tk),
            "lobbying":  pull_lobbying_for_ticker(tk),
            "contracts": pull_contracts_for_ticker(tk),
        }
        time.sleep(0.3)  # gentle on Quiver's rate limits

    _save("enrichment", enrichment)
    return enrichment

def main():
    print(f"=== Quiver pull starting @ {RUN_STAMP} UTC ===")
    congress = pull_congress(lookback_days=30)
    darkpool = pull_darkpool()
    enrichment = enrich_top_tickers(congress) if congress else {}
    print(f"=== Pull complete. Files in {DATA_DIR} ===")
    return {"congress": congress, "darkpool": darkpool, "enrichment": enrichment}

if __name__ == "__main__":
    main()
