# Notes for Claude

## How Beth likes to work
- **Push each change right away** so Beth can test it on the phone. If Beth says "hold", commit locally and wait until told to push.
- After pushing, don't schedule a follow-up check that the publish went live (it costs usage, and publishing is reliable). Beth taps Refresh when the "Update available" banner appears.
- When Beth asks for options or "what do you think", explain the options and a recommendation **before** building anything.
- Keep explanations plain and non-technical.
- Never ask for API keys in chat. Keys live only in GitHub Secrets: `TMDB_API_KEY`, `TRAKT_CLIENT_ID`, `OMDB_API_KEY`, `MDBLIST_API_KEY`.

## The app
- Phone web app ("Tivvy", formerly "Sofari", "Cue", "Up Next" and "What to Watch"), mostly used as an iPhone home-screen app.
- Live at https://bfullest7.github.io/tivvy/ (the address is case-sensitive). Repo: `bfullest7/tivvy` (renamed from `TV-recommendations`), branch `main`.
- Files: `index.html`, `app.js`, `styles.css`, `sw.js` (background helper: always loads the newest page, and keeps its own copy of the newest ~600 posters and cast photos, since iPhone didn't keep them between launches), `manifest.webmanifest`, `icons/`, `config.json` (services, score weights, row settings; read live by the app).
- Data: `pipeline/build_feed.py` (Python standard library only) builds `data/feed.json` (the file every phone downloads; keep it lean: about 1.7 MB compressed in Nov 2026) and `data/cast/NN.json` (cast, 100 small files loaded when a title's screen opens) from TMDB, Trakt, IMDb, OMDb, MDBList and Netflix Top 10. Tests: `python3 -m unittest discover -s tests`.
- `README.md` explains the rows, scoring and setup.
- `ALGORITHM.md` is the single reference for how titles are picked and ordered (data, rows, Top Picks, likes and dislikes, rotation). Update it, including its "Changes and why" log, whenever an algorithm rule or number changes.
- `APP_STORE.md` covers what it would take to publish Tivvy on the iOS App Store (conversion, Apple review, data-source licensing). Re-check the terms before acting.
- `DESIGN.md` is the design system: text sizes, colors, buttons, spacing and wording rules. Follow it for visual or wording changes, and update it when Beth approves a new rule.

## Publishing
- `.github/workflows/refresh.yml` refreshes the data every 2 hours and publishes the site on every push (data-only commits are skipped).
- On publish, the app version is a fingerprint of `index.html`, `app.js` and `styles.css`. It's stamped into the page and `version.json`, which drives the "Update available" banner.
- New site files must be added to the workflow's "Collect site files" copy step.
- **Keep the data download small.** `data/feed.json` is downloaded by every phone (1.7 MB compressed in mid-Nov 2026). Before adding a tracked list, a service or a new field per title, estimate how much it adds and tell Beth; put data only a title's screen needs in separate files (like `data/cast/`). The workflow's "Check download size" step fails the run (GitHub emails Beth) when it passes `feed_size_alarm_mb` in config.json (2.5); the data still publishes. If that email comes, find what grew and trim it.
- Don't push while a manually started data refresh is still queued: a newer run cancels the pending one.

## iPhone home-screen quirks (already handled; be careful changing)
- The status bar is black-translucent with `viewport-fit=cover`. On a fresh launch, iOS briefly doubles the status-bar space. `settleLayout()` in `app.js` corrects it in the first 0.3 seconds.
- A launch screen (`#splash` in `index.html`) hides that correction: a white screen, then the logo fades in at about 0.4 seconds (once it holds still; skipped if that takes past 1.6 seconds), then a 0.15-second fade to the dark app 0.75 seconds later, or, if the posters on the first screen haven't downloaded yet, as soon as they have (at most 1 second more, so about 2.2 seconds at the very most; a safety net reveals the app at 3.5 seconds no matter what). The launch screen is sized to the phone's screen height (`--splash-h`), not the page: a launch log on Beth's iPhone showed iOS sometimes keeps the page 62px short (the status-bar height) until about 1 second, and the settle trick doesn't always fix it, so a page-centered logo jumped down 31px when iOS caught up. If the jump comes back, the temporary launch log (commit 68beec5: per-frame measurements for the first 1.8 seconds, shown at the bottom of Profile) can be restored to diagnose it. It applies only when `navigator.standalone` is true.
- The launch screen is white on purpose: iOS always shows a white screen while starting the app, and a matching white launch screen hides that. Dark `apple-touch-startup-image` launch images were tried and removed: iOS still flashed white before them.
- The header has 24px of extra top padding so the title clears the iOS status-bar fade.
