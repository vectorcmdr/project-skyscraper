"""Regenerate feed.json from state, then print first 20 entries for review."""
import sys, json, urllib.parse
from pathlib import Path

# Ensure we can import from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor.config import DATA_DIR
from monitor.feed_manager import seed_feed_from_mirror, generate_site_data
from monitor.state_manager import load_state

DATA_DIR.mkdir(parents=True, exist_ok=True)
state = load_state()

# Run seed — this will create feed.json from scratch with all API items
print("Running seed_feed_from_mirror ...")
seed_feed_from_mirror(state)
print("Seed complete.")

# Read back and show first 20
feed_path = DATA_DIR / "feed.json"
if feed_path.is_file():
    feed = json.loads(feed_path.read_text(encoding="utf-8"))
    entries = feed.get("entries", [])
    print(f"\nTotal entries: {len(entries)}")
    print(f"\n{'='*80}")
    print("FIRST 20 ENTRIES (newest first):")
    print(f"{'='*80}")
    for i, e in enumerate(entries[:20]):
        ts = e.get("timestamp", "?")[:19]
        typ = e.get("type", "?")
        title = (e.get("title") or e.get("detail") or "?")[:60]
        game = e.get("game_date", "")
        print(f"  {i+1:2d}. {ts}  {typ:30s}  {title}")
        if game:
            print(f"      game_date: {game[:19]}")
    print(f"\n{'='*80}")
    print(f"Last 5 entries (oldest):")
    print(f"{'='*80}")
    for e in entries[-5:]:
        ts = e.get("timestamp", "?")[:19]
        typ = e.get("type", "?")
        title = (e.get("title") or e.get("detail") or "?")[:60]
        print(f"  {ts}  {typ:30s}  {title}")
else:
    print("ERROR: feed.json was not created!")
