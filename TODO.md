# TODO - MapHarvest scraping stall fix

- [ ] Patch `core/scraper.py` progressive scrolling: safer end-of-list detection
- [ ] Patch `core/scraper.py` wait-for-more logic to use href/key growth (not just visible count)
- [ ] Add robust scroll trigger fallback (window scrollBy / keyboard) when feed scroll doesn’t load
- [ ] After closing business tab, stabilize feed (wait for feed + small scroll jitter)
- [ ] Improve stall handling: keep scrolling before concluding end
- [ ] Quick sanity test run (max_results 20-50) to confirm it continues past prior ~16-20 stop

