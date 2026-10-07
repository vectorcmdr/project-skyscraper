"""Per-post and cycle classifier for memory-bloc wave quiescence.

Stateless functions that compare a STATE_A and STATE_B snapshot for a
/wp-json/wp/v2/posts cycle and classify:

  - per-post: which transitions happened (NO_CHANGE, SHIFT, REVERT,
    NON_BLOC_CHANGE, ANOMALOUS_BLOC)
  - cycle: overall type (trivial, normal_round_trip, normal_dribble,
    page_change_with_pattern, page_change_only, anomalous_cycle)

The classifier operates on full snapshot items (with content.rendered)
saved by api_collections._save_collection_snapshot. It does not need
per-item fetches at the daemon level.
"""
import json
import re
from pathlib import Path

MULTIPLIER = 1.0016
TOLERANCE = 1e-6

_BLOC_RE = re.compile(r"memory bloc (-?\d+)")


def parse_bloc(text):
    """Extract the numeric memory-bloc value from content.rendered text.

    Works for both colon ('memory bloc 1785088156:') and no-colon
    ('memory bloc 1785088156<br>') formats.
    """
    if not text:
        return None
    m = _BLOC_RE.search(text)
    return int(m.group(1)) if m else None


def _is_shift(a, b):
    return abs(b / a - MULTIPLIER) < TOLERANCE or b == round(a * MULTIPLIER)


def _is_revert(a, b):
    return abs(a / b - MULTIPLIER) < TOLERANCE or a == round(b * MULTIPLIER)


def classify_post(a_render, b_render):
    """Classify one post's transition between A and B content.rendered.

    Returns a SET of transition types. A post can carry multiple, e.g.
    {SHIFT, NON_BLOC_CHANGE} for a wave + value rewrite in the same cycle.
    """
    if a_render == b_render:
        return {"NO_CHANGE"}

    a_bloc = parse_bloc(a_render)
    b_bloc = parse_bloc(b_render)

    if a_bloc is None or b_bloc is None:
        return {"NON_BLOC_CHANGE"}
    if a_bloc == b_bloc:
        return {"NON_BLOC_CHANGE"}

    transitions = set()
    if _is_shift(a_bloc, b_bloc):
        transitions.add("SHIFT")
    elif _is_revert(a_bloc, b_bloc):
        transitions.add("REVERT")
    else:
        transitions.add("ANOMALOUS_BLOC")

    # Detect non-bloc change alongside a bloc change (e.g. value section
    # rewritten during a wave). Compare the rendered text minus the bloc line.
    if transitions & {"SHIFT", "REVERT", "ANOMALOUS_BLOC"}:
        # Strip the bloc number from both renders before comparison.
        a_stripped = _BLOC_RE.sub("__BLOC__", a_render)
        b_stripped = _BLOC_RE.sub("__BLOC__", b_render)
        if a_stripped != b_stripped:
            transitions.add("NON_BLOC_CHANGE")

    return transitions


def _get_render(item):
    c = item.get("content", {})
    if isinstance(c, dict):
        return c.get("rendered", "") or ""
    return ""


def aggregate_cycle(per_post):
    """Aggregate per-post transitions into a single cycle type and
    a list of breaking post ids.
    """
    if not per_post:
        return "trivial", []

    all_trans = set()
    for t in per_post.values():
        all_trans.update(t)

    if "ANOMALOUS_BLOC" in all_trans:
        breaking = [pid for pid, t in per_post.items() if "ANOMALOUS_BLOC" in t]
        return "anomalous_cycle", breaking

    has_non_bloc = "NON_BLOC_CHANGE" in all_trans
    has_shift = "SHIFT" in all_trans
    has_revert = "REVERT" in all_trans

    if not has_shift and not has_revert:
        if has_non_bloc:
            return "page_change_only", [
                pid for pid, t in per_post.items() if "NON_BLOC_CHANGE" in t
            ]
        return "trivial", []

    if has_shift and not has_revert:
        if has_non_bloc:
            return "page_change_with_pattern", [
                pid for pid, t in per_post.items() if "NON_BLOC_CHANGE" in t
            ]
        return "normal_round_trip", []

    if has_revert and not has_shift:
        if has_non_bloc:
            return "page_change_with_pattern", [
                pid for pid, t in per_post.items() if "NON_BLOC_CHANGE" in t
            ]
        return "normal_round_trip", []

    # Both SHIFT and REVERT present
    if has_non_bloc:
        return "page_change_with_pattern", [
            pid for pid, t in per_post.items() if "NON_BLOC_CHANGE" in t
        ]
    return "normal_dribble", []


def load_snapshot_items(path):
    """Load a snapshot file and return {post_id_str: full_item}.

    Returns {} for missing or unreadable files.
    """
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        with p.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return {
        str(it.get("id")): it
        for it in data.get("items", [])
        if it.get("id") is not None
    }


def classify_cycle(prev_items, cur_items, item_ids):
    """Classify a /wp-json/wp/v2/posts cycle.

    prev_items: dict of {post_id_str: full_item} from STATE_A snapshot,
                or None if no STATE_A (first cycle after daemon restart).
    cur_items:  dict of {post_id_str: full_item} from the current snapshot.
    item_ids:   list of post IDs that changed in this cycle (from the
                change dict's items list).

    Returns (cycle_type, breaking_post_ids).
    """
    if prev_items is None:
        # No STATE_A: first cycle, can't classify. Default to
        # page_change_with_pattern (safe default that still surfaces).
        return "page_change_with_pattern", [str(i) for i in item_ids]

    per_post = {}
    for pid in item_ids:
        pid_str = str(pid)
        a_item = prev_items.get(pid_str)
        b_item = cur_items.get(pid_str)
        if a_item is None or b_item is None:
            continue
        per_post[pid_str] = classify_post(_get_render(a_item), _get_render(b_item))

    return aggregate_cycle(per_post)


def is_silent(cycle_type):
    """Cycle types that should be silent (no Discord, no git push)."""
    return cycle_type in ("trivial", "normal_round_trip", "normal_dribble")


def is_alert(cycle_type):
    """Cycle types that should produce an alert."""
    return cycle_type in (
        "anomalous_cycle",
        "page_change_with_pattern",
        "page_change_only",
    )
