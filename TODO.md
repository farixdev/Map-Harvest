# MapHarvest — TODO

## Done — card-first rewrite
- [x] Replace "open a new tab per business" with single-pass feed-card parsing (`core/parse.py`)
- [x] Decode coordinates + Place ID from each listing URL (new export fields)
- [x] Detect & skip sponsored/ad cards; de-duplicate by Place ID
- [x] Detail-page fallback used only when a requested field is missing from the card
- [x] Stream complete rows live; fetch gaps in a second pass
- [x] Offline parser tests against a captured feed (`tests/test_parse.py`)
- [x] Live smoke test — service query (0 detail visits, ~2s/4) and POI query (fallback fills phone/website)
- [x] Remove dead scratch files; add `.gitignore`; stop tracking `__pycache__`

## Done — power features
- [x] Fixed scroll pagination (WheelEvent on div[role="feed"]) — was capping at ~7
- [x] Website enrichment: email + social links (`core/enrich.py`, stdlib only)
- [x] Result filters: rating, reviews, has/no website, phone, email, name/category (`core/filters.py`)
- [x] Multi-city mode — domain × area matrix (`ScrapeWorker` + Area list dialog)
- [x] Power results table — live search, sortable columns, right-click row actions, on-demand CSV export
- [x] Offline tests for enrich + filters; off-screen Qt UI test; live filter+enrich smoke

## Ideas / future
- [ ] Concurrent detail fallback / concurrent enrichment for POI-heavy or email-heavy runs
- [ ] Parse `window.APP_INITIALIZATION_STATE` to recover phone/website without any detail visit
- [ ] Auto-split big cities into sub-areas to beat Google's ~120-per-search cap
- [ ] Persist to SQLite + resume/append across sessions; Excel/JSON export
- [ ] Make "no website" filter detail-accurate for POI (currently card-level)
