- [ ] Inspect current scraping flow and identify failures
- [x] Remove duplicate `_extract_business_data_in_new_tab` definition in `core/scraper.py`

- [x] Implement scroll warm-up (2–3 steps) before scraping listings
- [x] Change listing selection to use ordered href snapshot per iteration
- [x] Mark listing as processed only after successful extraction
- [x] Add retry-on-failure per listing href to avoid infinite loops

- [x] Ensure deterministic tab close and switch-back to original handle

- [ ] Smoke-test scrape for one domain with small max_results

