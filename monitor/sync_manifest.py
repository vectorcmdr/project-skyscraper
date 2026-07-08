"""Re-run manifest sync from sitemap, then verify and show summary."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor.config import DATA_DIR
from monitor.feed_manager import _sync_manifest_from_sitemap, _write_if_changed
from monitor.state_manager import load_state

state = load_state()

manifest_path = DATA_DIR / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
before_count = len(manifest.get("pages", []))

_sync_manifest_from_sitemap(manifest, state)

after_count = len(manifest.get("pages", []))
added = after_count - before_count

if added:
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Added {added} missing page(s) to manifest from sitemap")
else:
    print("Manifest already in sync with sitemap")

print(f"\nManifest: {after_count} pages total")
print(f"Feed: {len(json.loads((DATA_DIR / 'feed.json').read_text(encoding='utf-8')).get('entries',[]))} entries")

# Show any recently-added manifest entries
if added:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(f"\nNewly added pages:")
    for p in manifest.get("pages", [])[-added:]:
        print(f"  {p['path']:60s}  {p['type']:10s}  modified={p['modified'][:19]}")
