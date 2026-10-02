"""
Pinterest reorg CLI. See README.md for setup.

  python main.py login
  python main.py list-boards
  python main.py fetch-cache
  python main.py find-duplicates [--new-only is default; --include-reviewed to re-show old groups]
  python main.py review-duplicates
  python main.py move --from "Old Board" --to "New Board"
  python main.py rename-board --board "Old name" --new-name "..." --new-description "..."
  python main.py rename-pin --pin-id 123 --title "..." --description "..."

Env vars: PINTEREST_APP_ID, PINTEREST_APP_SECRET, PINTEREST_REDIRECT_URI
"""
import argparse
import json
import os
import sys
from pathlib import Path

from dedupe import exact_duplicate_groups, group_signature, repetitive_title_groups, visual_duplicate_groups
from pinterest_client import PinterestClient

CACHE_FILE = Path(__file__).parent / "cache.json"
RESOLVED_FILE = Path(__file__).parent / "resolved_state.json"  # signatures of groups already reviewed
REVIEW_BATCH_SIZE = 20  # confirm after every N deletions, per your own "review early batches" rule


def _dump(obj) -> str:
    """Compact JSON — smaller files, faster to read/paste/retrieve than indent=2."""
    return json.dumps(obj, separators=(",", ":"))


def _load_resolved() -> set:
    if RESOLVED_FILE.exists():
        return set(json.loads(RESOLVED_FILE.read_text()))
    return set()


def _save_resolved(sigs: set):
    RESOLVED_FILE.write_text(_dump(sorted(sigs)))


def get_client() -> PinterestClient:
    temp_token = os.environ.get("PINTEREST_ACCESS_TOKEN")
    if temp_token:
        return PinterestClient(access_token=temp_token)

    app_id = os.environ.get("PINTEREST_APP_ID")
    app_secret = os.environ.get("PINTEREST_APP_SECRET")
    redirect_uri = os.environ.get("PINTEREST_REDIRECT_URI")
    missing = [n for n, v in [("PINTEREST_APP_ID", app_id), ("PINTEREST_APP_SECRET", app_secret),
                              ("PINTEREST_REDIRECT_URI", redirect_uri)] if not v]
    if missing:
        sys.exit(f"Missing environment variables: {', '.join(missing)}. See README.md.")
    return PinterestClient(app_id, app_secret, redirect_uri)


def cmd_login(client: PinterestClient):
    url = client.build_authorize_url()
    print("1. Open this URL in your phone's browser and approve access:\n")
    print(f"   {url}\n")
    print("2. Pinterest will redirect you to your redirect URI with ?code=XXXX in the address bar.")
    print("   Copy just the code value (everything after 'code=', before any '&').\n")
    code = input("Paste the code here: ").strip()
    client.exchange_code_for_token(code)
    print("Logged in. Token saved to token.json (do not commit this file).")


def cmd_list_boards(client: PinterestClient):
    boards = client.list_boards()
    for b in boards:
        print(f"{b['id']}\t{b['name']}\t({b.get('pin_count', '?')} pins)")
    print(f"\n{len(boards)} boards total.")


def cmd_fetch_cache(client: PinterestClient):
    print("Fetching boards...")
    boards = client.list_boards()
    print(f"  {len(boards)} boards. Fetching pins per board (this can take a while)...")
    pins = client.list_all_pins(boards)
    CACHE_FILE.write_text(_dump({"boards": boards, "pins": pins}))
    print(f"  {len(pins)} pins cached to {CACHE_FILE.name}.")


def _load_cache():
    if not CACHE_FILE.exists():
        sys.exit("No cache found. Run: python main.py fetch-cache")
    return json.loads(CACHE_FILE.read_text())


def _group_entry(group, extra_fields=()):
    sig = group_signature(group)
    pins = [
        {"id": p["id"], "title": p.get("title"), "board": p.get("_board_name"),
         **{f: p.get(f) for f in extra_fields}}
        for p in group
    ]
    return sig, pins


def cmd_find_duplicates(args):
    data = _load_cache()
    pins = data["pins"]
    resolved = _load_resolved() if not args.include_reviewed else set()

    report = {"visual_similarity": [], "exact_link_or_image": [], "repetitive_titles": []}
    new_count = 0
    total_count = 0

    # Visual (image) comparison is the main pass — runs across every board,
    # not just within one, since that's where most of your duplicates are.
    if not args.no_visual:
        print(f"Comparing images across all {len(pins)} pins (within and across boards)...")
        for group in visual_duplicate_groups(pins, max_distance=args.distance, workers=args.workers):
            sig, entry = _group_entry(group)
            total_count += 1
            if sig in resolved:
                continue
            new_count += 1
            report["visual_similarity"].append({"sig": sig, "pins": entry})

    for group in exact_duplicate_groups(pins):
        sig, entry = _group_entry(group, extra_fields=("link",))
        total_count += 1
        if sig in resolved:
            continue
        new_count += 1
        report["exact_link_or_image"].append({"sig": sig, "pins": entry})

    for group in repetitive_title_groups(pins):
        sig, entry = _group_entry(group)
        total_count += 1
        if sig in resolved:
            continue
        new_count += 1
        report["repetitive_titles"].append({"sig": sig, "pins": entry})

    out_file = Path(__file__).parent / "duplicates_report.json"
    out_file.write_text(_dump(report))

    if not args.no_visual:
        print(f"Visually-similar groups (images): {len(report['visual_similarity'])}")
    print(f"Exact link/image-URL duplicate groups: {len(report['exact_link_or_image'])}")
    print(f"Repetitive-title groups: {len(report['repetitive_titles'])}")
    if not args.include_reviewed:
        print(f"({total_count - new_count} previously-reviewed groups skipped; showing {new_count} new)")
    print(f"\nFull details written to {out_file.name}. Nothing was changed or deleted.")
    print("Next: python main.py review-duplicates")


def _all_report_groups(report):
    groups = []
    for key in ("visual_similarity", "exact_link_or_image", "repetitive_titles"):
        for g in report.get(key, []):
            groups.append((key, g["sig"], g["pins"]))
    return groups


def cmd_review_duplicates(client: PinterestClient):
    report_file = Path(__file__).parent / "duplicates_report.json"
    if not report_file.exists():
        sys.exit("No report found. Run: python main.py find-duplicates first.")
    report = json.loads(report_file.read_text())
    groups = _all_report_groups(report)
    if not groups:
        print("No duplicate/repetitive groups found. Nothing to review.")
        return

    resolved = _load_resolved()
    to_delete = []
    print(f"{len(groups)} groups to review. For each, pick which pin(s) to KEEP; the rest are marked for deletion.")
    print("Commands: comma-separated numbers to keep, 's' to skip (asks again next time), 'q' to stop reviewing.\n")

    for idx, (kind, sig, group) in enumerate(groups, 1):
        print(f"--- Group {idx}/{len(groups)} [{kind}] ---")
        for n, p in enumerate(group, 1):
            print(f"  {n}. id={p['id']}  board={p.get('board')}  title={p.get('title')!r}")
        choice = input("Keep which # (e.g. 1)? [s=skip, q=quit]: ").strip().lower()
        if choice == "q":
            break
        if choice == "s" or not choice:
            continue
        keep_idx = {int(x) for x in choice.split(",") if x.strip().isdigit()}
        for n, p in enumerate(group, 1):
            if n not in keep_idx:
                to_delete.append(p)
        resolved.add(sig)  # decided (kept a subset) — won't resurface unless the group's pins change
        print()

    _save_resolved(resolved)

    if not to_delete:
        print("Nothing marked for deletion.")
        return

    print(f"\n{len(to_delete)} pins marked for deletion.")
    for batch_start in range(0, len(to_delete), REVIEW_BATCH_SIZE):
        batch = to_delete[batch_start : batch_start + REVIEW_BATCH_SIZE]
        print(f"\nBatch {batch_start // REVIEW_BATCH_SIZE + 1}: {len(batch)} pins")
        for p in batch:
            print(f"  - id={p['id']}  board={p.get('board')}  title={p.get('title')!r}")
        confirm = input("Delete this batch? [y/N]: ").strip().lower()
        if confirm != "y":
            print("Skipped this batch.")
            continue
        for p in batch:
            try:
                client.delete_pin(p["id"])
                print(f"  deleted {p['id']}")
            except Exception as e:
                print(f"  FAILED to delete {p['id']}: {e}")


def cmd_move(client: PinterestClient, args):
    boards = client.list_boards()
    by_name = {b["name"]: b for b in boards}
    if args.frm not in by_name:
        sys.exit(f"Board not found: {args.frm}")
    if args.to not in by_name:
        sys.exit(f"Board not found: {args.to}")
    src, dst = by_name[args.frm], by_name[args.to]
    pins = client.list_pins(board_id=src["id"])
    print(f"Moving {len(pins)} pins from '{args.frm}' to '{args.to}'...")
    confirm = input(f"Confirm move of {len(pins)} pins? [y/N]: ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return
    for p in pins:
        try:
            client.move_pin(p["id"], dst["id"])
            print(f"  moved {p['id']}")
        except Exception as e:
            print(f"  FAILED to move {p['id']}: {e}")


def cmd_rename_board(client: PinterestClient, args):
    boards = client.list_boards()
    match = next((b for b in boards if b["name"] == args.board), None)
    if not match:
        sys.exit(f"Board not found: {args.board}")
    client.rename_board(match["id"], name=args.new_name, description=args.new_description)
    print("Board updated.")


def cmd_rename_pin(client: PinterestClient, args):
    client.rename_pin(args.pin_id, title=args.title, description=args.description)
    print("Pin updated.")


def main():
    parser = argparse.ArgumentParser(description="Pinterest reorg tool")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login")
    sub.add_parser("list-boards")
    sub.add_parser("fetch-cache")

    p_find = sub.add_parser("find-duplicates")
    p_find.add_argument("--no-visual", action="store_true", help="skip image comparison, just run the fast link/title checks")
    p_find.add_argument("--distance", type=int, default=6, help="how similar images must be to count as duplicates; lower = stricter (default 6, try 4-8)")
    p_find.add_argument("--workers", type=int, default=8, help="parallel image downloads while hashing (default 8)")
    p_find.add_argument("--include-reviewed", action="store_true", help="also re-show groups you already reviewed before (default: only show new ones)")

    sub.add_parser("review-duplicates")

    p_move = sub.add_parser("move")
    p_move.add_argument("--from", dest="frm", required=True)
    p_move.add_argument("--to", required=True)

    p_rb = sub.add_parser("rename-board")
    p_rb.add_argument("--board", required=True)
    p_rb.add_argument("--new-name")
    p_rb.add_argument("--new-description")

    p_rp = sub.add_parser("rename-pin")
    p_rp.add_argument("--pin-id", required=True)
    p_rp.add_argument("--title")
    p_rp.add_argument("--description")

    args = parser.parse_args()

    if args.command == "login":
        cmd_login(get_client())
    elif args.command == "list-boards":
        cmd_list_boards(get_client())
    elif args.command == "fetch-cache":
        cmd_fetch_cache(get_client())
    elif args.command == "find-duplicates":
        cmd_find_duplicates(args)
    elif args.command == "review-duplicates":
        cmd_review_duplicates(get_client())
    elif args.command == "move":
        cmd_move(get_client(), args)
    elif args.command == "rename-board":
        cmd_rename_board(get_client(), args)
    elif args.command == "rename-pin":
        cmd_rename_pin(get_client(), args)


if __name__ == "__main__":
    main()
