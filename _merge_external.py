"""Merge external.json entries from git history + current file + dedup."""
import json, subprocess, sys
from pathlib import Path

current_path = Path("docs/data/external.json")

# Recover old external.json from git
result = subprocess.run(
    ["git", "show", "2c49966^:docs/data/external.json"],
    capture_output=True, text=True, encoding="utf-8", errors="replace"
)
old_data = json.loads(result.stdout)

current_data = json.loads(current_path.read_text(encoding="utf-8"))

all_entries = []
seen_keys = set()

for e in current_data["entries"] + old_data["entries"]:
    k = (e["type"], e.get("site", ""), e.get("detail", "")[:120])
    if k not in seen_keys:
        seen_keys.add(k)
        all_entries.append(e)

all_entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
all_entries = all_entries[:500]

merged = {"entries": all_entries, "updated": current_data["updated"]}
current_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Merged: {len(current_data['entries'])} current + {len(old_data['entries'])} old = {len(all_entries)} unique entries")
