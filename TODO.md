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

## Ideas / future
- [ ] Concurrent detail fallback (2–3 place pages in parallel) for POI-heavy searches
- [ ] Parse `window.APP_INITIALIZATION_STATE` to recover phone/website without any detail visit
- [ ] Optional: capture opening-hours JSON directly from the card where present
- [ ] Resume/append to an existing CSV across sessions
