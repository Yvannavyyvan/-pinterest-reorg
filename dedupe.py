"""Duplicate/repetitive-pin detection. Never deletes — only produces groups to review.
1) VISUAL (main pass): perceptual image hash, compared account-wide (within+across boards),
   cached in image_hash_cache.json so re-runs only hash new pins.
2) EXACT (fast): same link or same image URL.
3) Repetitive title: same title + same board."""
import io
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

HASH_CACHE_FILE = Path(__file__).parent / "image_hash_cache.json"


def _image_url(pin: dict) -> str | None:
    media = pin.get("media") or {}
    images = media.get("images") or {}
    # Pinterest returns several sizes; take the largest available.
    for key in ("originals", "1200x", "600x", "400x300", "150x150"):
        if key in images and images[key].get("url"):
            return images[key]["url"]
    return None


def exact_duplicate_groups(pins: list) -> list[list[dict]]:
    by_key = defaultdict(list)
    for p in pins:
        link = (p.get("link") or "").strip().rstrip("/")
        img = _image_url(p)
        if link:
            by_key[("link", link)].append(p)
        elif img:
            by_key[("img", img)].append(p)
    return [group for group in by_key.values() if len(group) > 1]


def group_signature(group: list[dict]) -> str:
    """Stable id for a group, used to detect *new* duplicate groups on later runs."""
    return "|".join(sorted(str(p["id"]) for p in group))


def repetitive_title_groups(pins: list) -> list[list[dict]]:
    """Same title AND same board — often the sign of an accidental repeat save."""
    by_key = defaultdict(list)
    for p in pins:
        title = (p.get("title") or "").strip().lower()
        if title:
            by_key[(title, p.get("_board_id"))].append(p)
    return [group for group in by_key.values() if len(group) > 1]


def _load_hash_cache() -> dict:
    if HASH_CACHE_FILE.exists():
        return json.loads(HASH_CACHE_FILE.read_text())
    return {}


def _save_hash_cache(cache: dict):
    HASH_CACHE_FILE.write_text(json.dumps(cache, separators=(",", ":")))


def _hash_one(pin_id: str, url: str, timeout: int) -> str:
    import imagehash
    from PIL import Image

    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    img = Image.open(io.BytesIO(resp.content))
    return str(imagehash.phash(img))


def build_image_hashes(pins: list, timeout: int = 10, workers: int = 8, progress: bool = True) -> dict:
    """Returns {pin_id: hash_string}; skips pins already cached. Requires imagehash+Pillow."""
    cache = _load_hash_cache()
    todo = []
    for p in pins:
        pid = p["id"]
        url = _image_url(p)
        if not url:
            continue
        cached = cache.get(pid)
        if cached and cached.get("url") == url:
            continue
        todo.append((pid, url))

    if todo:
        if progress:
            print(f"  hashing {len(todo)} new/changed images ({len(pins) - len(todo)} already cached)...")
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_hash_one, pid, url, timeout): (pid, url) for pid, url in todo}
            done = 0
            for fut in as_completed(futures):
                pid, url = futures[fut]
                done += 1
                try:
                    h = fut.result()
                    cache[pid] = {"url": url, "hash": h}
                except Exception as e:
                    print(f"  [skip] could not hash pin {pid}: {e}")
                if progress and done % 25 == 0:
                    print(f"    ...{done}/{len(todo)}")
        _save_hash_cache(cache)

    return {pid: entry["hash"] for pid, entry in cache.items() if pid in {p["id"] for p in pins}}


def visual_duplicate_groups(pins: list, max_distance: int = 6, timeout: int = 10, workers: int = 8) -> list[list[dict]]:
    """Clusters pins whose image hashes are within max_distance (lower=stricter; try 4-8)."""
    import imagehash

    pin_hashes = build_image_hashes(pins, timeout=timeout, workers=workers)
    pins_by_id = {p["id"]: p for p in pins}

    items = [(pins_by_id[pid], imagehash.hex_to_hash(h)) for pid, h in pin_hashes.items() if pid in pins_by_id]

    groups = []
    used = set()
    for i, (pin_a, hash_a) in enumerate(items):
        if pin_a["id"] in used:
            continue
        cluster = [pin_a]
        for pin_b, hash_b in items[i + 1 :]:
            if pin_b["id"] in used:
                continue
            if hash_a - hash_b <= max_distance:
                cluster.append(pin_b)
                used.add(pin_b["id"])
        if len(cluster) > 1:
            used.add(pin_a["id"])
            groups.append(cluster)
    return groups
