# Pinterest reorg script

A plain script (no AI chat loop, no third-party server) that talks directly to
the Pinterest API using your own developer app credentials. Runs entirely in
a GitHub Codespaces browser terminal, which works fine from iPhone Safari.

Nothing here deletes or moves anything without asking you to confirm first.
Duplicate/repetitive detection only ever produces a report for you to review —
you choose what gets deleted.

## 1. Put this code in a GitHub repo

1. On github.com (Safari is fine), create a new **private** repository, e.g. `pinterest-reorg`.
2. Use the "Add file → Upload files" button to upload all the files in this folder
   (`main.py`, `pinterest_client.py`, `dedupe.py`, `requirements.txt`, `.gitignore`, this `README.md`).
3. Commit them to the `main` branch.

## 2. Open it in a Codespace

1. On the repo page, tap the green **Code** button → **Codespaces** tab → **Create codespace on main**.
2. Wait for it to spin up — you'll land in a browser-based VS Code with a terminal at the bottom.
3. In the terminal, install dependencies:
   ```
   pip install -r requirements.txt
   ```

## 3. Finish setting up your Pinterest developer app

You already created a Client ID / Secret earlier. Two things still needed:

**a. Redirect URI.** In the Codespace terminal, run:
```
python -m http.server 8000
```
Codespaces will pop up a notification with a forwarded URL that looks like
`https://<random-name>-8000.app.github.dev`. Copy that base URL, then in your
Pinterest app's settings (developers.pinterest.com → your app → Redirect URIs),
add:
```
https://<random-name>-8000.app.github.dev/callback
```
Save it. You can stop the `http.server` command afterwards (Ctrl+C) — it was
only running so Codespaces would generate and forward that URL for you to copy.

**b. API access level.** Your app currently has "Trial" access. For acting on
*your own* account (not other users'), Trial access already covers reading and
writing boards and pins — you do not need to wait for "Standard" approval to
use this script on your own account. If you hit a permission error on a
specific call, that's the signal to request Standard access at that point —
no need to apply preemptively.

## 4. Set your credentials

In the Codespace terminal:
```
export PINTEREST_APP_ID="your-client-id"
export PINTEREST_APP_SECRET="your-client-secret"
export PINTEREST_REDIRECT_URI="https://<random-name>-8000.app.github.dev/callback"
```
(These only last for the current terminal session — re-run them if you close
and reopen the Codespace. Never commit these values into the repo.)

## 5. Log in once

```
python main.py login
```
This prints a Pinterest URL — open it on your phone, approve access, and
you'll be redirected to a page that may show as unreachable (that's fine, the
Codespace isn't serving anything there). Look at the address bar of that
redirected page, copy the `code=...` value, and paste it back into the
terminal when prompted. A `token.json` is saved locally (git-ignored, never
uploaded) and auto-refreshes after that — you won't need to log in again.

## 6. Everyday commands

```
python main.py list-boards
python main.py fetch-cache                       # pulls all boards + pins into cache.json, needed before duplicate checks
python main.py find-duplicates                   # compares images across your ENTIRE account (within and across boards) by default
python main.py find-duplicates --distance 4      # stricter image match (fewer false positives, may miss some near-duplicates)
python main.py find-duplicates --no-visual       # skip image comparison, just run the fast link/title checks
python main.py review-duplicates                 # walks through each duplicate group, asks what to keep, deletes the rest in batches of 20 with a confirm prompt per batch
python main.py move --from "Old Board Name" --to "New Board Name"
python main.py rename-board --board "Old name" --new-name "New name" --new-description "..."
python main.py rename-pin --pin-id 123456789 --title "..." --description "..."
```

## What "duplicate/repetitive" means here

Since your duplicates are mainly the same image saved more than once — either
repeated within a board or spread across several — **image comparison is the
main tool and runs by default**:

- **Visual (default)**: downloads each pin's image and compares them all against
  each other account-wide using perceptual image hashing — it doesn't matter
  whether two copies are in the same board or in different boards, both get
  caught. It also catches re-saves from a different source link, or a
  slightly cropped/re-uploaded copy of the same picture. `--distance` controls
  how strict the match is (default 6; try 4 for stricter, 8 for looser).
  Images are hashed once and cached in `image_hash_cache.json`, so re-running
  `find-duplicates` later only hashes new pins, not everything again.
- **Exact** (fast, always runs too): two or more pins point at the exact same
  source link or the exact same image URL — a cheap supplement.
- **Repetitive title**: two or more pins on the *same board* with the identical
  title — a lightweight extra signal.

None of these are auto-deleted. `find-duplicates` only writes a report;
`review-duplicates` is the only command that can delete pins, and it asks you
to pick which pin to keep in every group, then confirms again before each
batch of 20 deletions actually runs.

**Finding *new* duplicates on later runs**: once you've reviewed a group in
`review-duplicates` (even if you chose to keep everything), it's remembered in
`resolved_state.json` and won't be shown again by default — so each future
`find-duplicates` run only surfaces duplicates introduced by pins added since
your last pass. Use `--include-reviewed` to see everything again from scratch.

**A note on time/rate limits**: comparing images means downloading every pin's
image at least once. For a large account (thousands of pins) the first
`find-duplicates` run can take a while and may hit Pinterest's rate limits —
the script already backs off and retries automatically on a 429, and cached
hashes mean you only pay that cost once.

## Safety notes carried over from planning

- Never put your Pinterest account password into this script or anywhere else — this uses OAuth only.
- You can revoke this app's access anytime from Pinterest → Settings → Apps.
- Keep your Client ID/Secret and `token.json` private — they're already excluded from git via `.gitignore`.
