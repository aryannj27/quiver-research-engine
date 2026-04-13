"""
run.py — End-to-end orchestration.

Usage:
    export QUIVER_API_KEY=...
    export ANTHROPIC_API_KEY=...
    python run.py

Runs pull -> score -> narrate -> brief and prints the path to the final HTML.
"""
import sys, time
import pull, score, narrate, brief

def main():
    t0 = time.time()
    print("\n" + "=" * 70)
    print(" QUIVER RESEARCH ENGINE — SUNDAY SPRINT")
    print("=" * 70 + "\n")

    # 1. Pull
    pull_result = pull.main()
    if not pull_result.get("congress"):
        print("\nABORT: no congress data pulled. Check API key / network.")
        sys.exit(1)

    # 2. Score
    print()
    leads = score.main()
    if not leads:
        print("\nNo leads surfaced. The system ran but found nothing above threshold.")
        print("(This is expected on quiet weeks. Playbook covers the empty-brief case.)")

    # 3. Narrate (optional — skips if ANTHROPIC_API_KEY missing)
    print()
    try:
        narrate.main()
    except SystemExit:
        print("[narrate] skipped (no API key). Brief will render without narrations.")

    # 4. Brief
    print()
    out = brief.main()
    elapsed = time.time() - t0
    print(f"\n✓ Pipeline complete in {elapsed:.1f}s")
    print(f"✓ Open: {out}")

if __name__ == "__main__":
    main()
