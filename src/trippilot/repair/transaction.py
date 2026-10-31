"""Atomic, version-checked edits addressed against an immutable plan snapshot.

This is deliberately a small domain edit language, not an RFC 6902 implementation.
IDs are snapshot day:index references; all removals/replacements resolve before
any array is changed. Therefore deleting a1 never makes a2 refer to old a3.
"""
import copy
import hashlib
import json

from ..plan.compare import activity_map


def revision(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


class EditRejected(ValueError):
    pass


def apply_edits(seed, expected_revision, edits, allowed_ids, keep_days=None):
    if expected_revision != revision(seed):
        raise EditRejected("stale plan revision")
    if not isinstance(edits, list) or len(edits) > 100:
        raise EditRejected("edits must be a list of at most 100 operations")
    original = activity_map(seed)
    allowed = set(allowed_ids)
    replacements, removals, additions = {}, set(), []
    for edit in edits:
        op, key = edit.get("op"), edit.get("id")
        if op in ("remove", "replace"):
            if key not in original or key not in allowed:
                raise EditRejected("unknown or protected activity: %s" % key)
            if key in replacements or key in removals:
                raise EditRejected("conflicting edits: %s" % key)
            if op == "remove":
                removals.add(key)
            else:
                if not isinstance(edit.get("value"), dict):
                    raise EditRejected("replacement must be an activity object")
                replacements[key] = copy.deepcopy(edit["value"])
        elif op == "append":
            day = edit.get("day")
            if not isinstance(day, int) or day < 1 or day > len(seed.get("itinerary", [])):
                raise EditRejected("invalid append day")
            # Insertion requires an explicitly granted per-day append capability.
            if "d%d:append" % day not in allowed or not isinstance(edit.get("value"), dict):
                raise EditRejected("append not permitted")
            additions.append((day, copy.deepcopy(edit["value"])))
        else:
            raise EditRejected("unsupported operation")
    days = len(seed.get("itinerary", []))
    if keep_days is not None:
        if not isinstance(keep_days, int) or not 1 <= keep_days <= days:
            raise EditRejected("invalid day truncation")
        for key in original:
            day = int(key.split(":")[0][1:])
            if day > keep_days and key not in allowed:
                raise EditRejected("truncation would delete protected activity")
        days = keep_days
    result = copy.deepcopy(seed)
    result["itinerary"] = result.get("itinerary", [])[:days]
    for d, day in enumerate(result["itinerary"], 1):
        day["activities"] = [replacements.get("d%d:a%d" % (d, a), value)
                             for a, value in enumerate(day.get("activities", []), 1)
                             if "d%d:a%d" % (d, a) not in removals]
        day["activities"].extend(value for target, value in additions if target == d)
    if any(day > days for day, _ in additions):
        raise EditRejected("append into removed day")
    return result


def patch_between(seed, candidate):
    """Diagnostic only: preservation counts use exact-content multiset matches.

    Counts do not mistake shifted array indices for changed activities. This
    reports removed/inserted content, not persistent semantic identity.
    """
    from collections import Counter
    before = Counter(revision(a) for a in activity_map(seed).values())
    after = Counter(revision(a) for a in activity_map(candidate).values())
    same = sum((before & after).values())
    return {"unchanged": same, "removed_or_modified": sum(before.values()) - same,
            "added_or_modified": sum(after.values()) - same}
