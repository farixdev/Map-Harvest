# Project Issues and Improvement Report

**Project:** MapHarvest (recommended replacement name: **LeadForge**)

**Review scope:** application source, UI, outreach pipeline, tests, build files,
and documentation. This is a logic and reliability review, not a live Gmail or
Google Maps penetration test.

**Verification:** the existing automated suite currently passes: **816 tests**.
That is strong evidence for the tested pure logic and UI contracts, but it does
not prove that Google Maps selectors, Gmail authentication, IMAP polling, or
arbitrary business websites work in production.

## Executive Summary

The application is well structured around a local-first pipeline:

```text
Google Maps -> parsed leads -> website enrichment/audit -> templates/AI
-> scheduled campaign -> Gmail -> SQLite history and suppression
```

The main logical risk is in the outreach lifecycle. A campaign is created before
its preparation finishes, preparation can write queue rows incrementally, and
the UI does not give the user a visible way to cancel preparation. A failed or
interrupted preparation can therefore leave a campaign and partial queue that
look valid enough to confuse the next action.

The second major risk is compliance state. Suppression and IMAP handling exist,
but account configuration and mailbox polling must remain correct for opt-outs,
bounces, and replies to affect already queued follow-ups.

## Severity Guide

- **Critical:** can send duplicate, unintended, or non-compliant email, or lose
  important suppression state.
- **High:** can leave the campaign in a misleading or unrecoverable state, or
  make a normal workflow appear broken.
- **Medium:** incorrect results, silent data loss, or substantial operational
  friction.
- **Low:** maintenance, documentation, naming, or edge-case reliability issue.

## Outreach Issues

### O-01: Preparation has no visible Cancel action

**Severity:** High

**Evidence:** `ui/screen_outreach.py` starts `_PlanWorker` and disables the
Prepare button, while `_PlanWorker.stop()` exists. The Campaign card exposes the
progress bar and Prepare button, but no Stop/Cancel button is shown while
`_planning` is true.

**Impact:** preparing a large list crawls websites and may call an AI provider.
The user can see progress but cannot intentionally stop the operation from the
screen. Closing the application is a harsh substitute and may leave partial
queue state.

**Recommended fix:** add a visible `Cancel preparation` action while planning.
Call `plan_worker.stop()`, disable Prepare, show `Cancelling`, and keep the
worker reference until `finished`. The result should clearly say how many
messages were already queued and how many leads were not processed.

### O-02: Campaign creation happens before preparation succeeds

**Severity:** High

**Evidence:** `_on_prepare_clicked()` calls `create_campaign()` before starting
`plan_campaign()`. The worker can later return an error, zero queued messages,
or a cancelled partial plan.

**Impact:** every failed attempt can leave a draft campaign in the database.
The campaign selector may show several empty or partial campaigns, and the
current campaign ID changes before there is a usable queue. Repeated retries are
likely to feel glitchy because the user cannot tell which campaign is valid.

**Recommended fix:** use an explicit `preparing` campaign status and show it in
the campaign list, or prepare into a transaction/staging table and publish the
campaign only after the plan succeeds. On a hard failure, mark the campaign
`failed` with the error rather than presenting it as a normal selectable
campaign. Add a cleanup/retry action.

### O-03: Preparation writes partial queue state incrementally

**Severity:** High

**Evidence:** `plan_campaign()` audits, renders, and then `_queue_pass()` writes
messages one by one. `should_stop()` can return true between rows, and the
already-written rows remain in the database by design.

**Impact:** cancellation is not atomic. This can be useful for recovery, but it
also means a user retrying preparation may see leads skipped as “already
contacted” or may not understand that only part of the intended list was
queued. A partial plan must never look like a complete plan.

**Recommended fix:** keep the partial behavior only if it is explicitly shown
as `partial/cancelled`, with counts for processed, queued, skipped, and
remaining. For an ordinary error, roll back newly queued rows for that planning
attempt. Store a `planning_run_id` or campaign version so cleanup cannot delete
messages from a previous valid run.

### O-04: The plan result can refresh several large surfaces at once

**Severity:** Medium

**Evidence:** `_on_plan_ready()` refreshes the campaign selector, reloads leads,
and refreshes statistics immediately after the worker emits its plan.

**Impact:** large lead stores can briefly freeze or visibly jump when planning
finishes. This is especially confusing because the network work was correctly
moved off the GUI thread, but the completion redraw can still be expensive.

**Recommended fix:** update the campaign summary first, then refresh only the
affected counters and current rows. Defer the full leads/stats reload with a
single queued UI update, and avoid rebuilding hidden tabs.

### O-05: Settings can change while a campaign is being prepared

**Severity:** Medium

**Evidence:** `_PlanWorker` receives a settings snapshot, but the main screen
can be sent to Settings while the worker is running. The resulting campaign is
created from the earlier snapshot, while later sending uses current settings.

**Impact:** the preview, plan summary, and actual queue can disagree about
sender profile, enabled accounts, compliance switches, follow-up rules, or
AI configuration. The database does snapshot profile/settings for campaigns,
but the user-facing state needs to make that boundary obvious.

**Recommended fix:** disable settings changes that affect an active preparation,
or explicitly label the campaign as using a captured snapshot. After preparation,
show the effective sender/accounts/rules from the campaign snapshot, not only
the current global settings.

### O-06: A successful plan can still contain zero or generic messages

**Severity:** Medium

**Evidence:** audit failures and rendering failures are counted per lead;
generic copy is allowed when personalization is unavailable; only queued count
and warnings determine whether the user proceeds.

**Impact:** a campaign may be technically “prepared” while a large portion is
form-letter copy or while many leads were silently excluded. The current summary
does report these counts, but the primary action does not require the user to
acknowledge an unusually poor result.

**Recommended fix:** add thresholds and explicit states: `ready`, `ready with
warnings`, and `no usable messages`. Display skipped reasons and generic-copy
percentage beside the final confirmation, and allow filtering/replanning only
the failed leads.

### O-07: Follow-up scheduling is sensitive to changed account availability

**Severity:** Medium

**Evidence:** follow-ups are initially assigned to an account/thread and the
send worker later re-evaluates account availability. If the original account is
disabled or retired, the worker may send without the original thread headers.

**Impact:** a follow-up can arrive as a new message rather than the expected
conversation. That is safer than sending a forged cross-account reply, but it
can surprise the user and recipient.

**Recommended fix:** show account changes in campaign warnings, preserve a
clear `threaded` versus `standalone follow-up` state, and let the user choose
whether affected follow-ups should be skipped instead of retargeted.

### O-08: Dry-run recovery is correct in intent but needs stronger user state

**Severity:** Medium

**Evidence:** rehearsed messages are restored to `queued`, and interrupted
rehearsals are recovered on the next run.

**Impact:** a crash or forced termination can leave the database in an interim
state until another send run starts. The Campaign/Sending screens may not
immediately explain that recovery is pending.

**Recommended fix:** run startup recovery when the Outreach screen opens, record
an event, and display a banner such as “Recovered N rehearsal messages; nothing
was sent.” Add a campaign-level `rehearsal_pending_recovery` state if needed.

### O-09: IMAP is optional even though it controls opt-out safety

**Severity:** Critical

**Evidence:** the footer and `List-Unsubscribe` route recipients to a mailbox,
but `imap_enabled` is per-account and can be disabled. The application warns
about unread opt-out routes, but sending can still proceed.

**Impact:** a recipient can reply “unsubscribe” and still receive queued
follow-ups if the mailbox is not read. This is a real compliance and reputation
risk, not merely a missing feature.

**Recommended fix:** make unread opt-out routes a hard block for live sending by
default. Permit an explicit override only after showing the affected addresses.
Keep dry runs available without IMAP.

### O-10: “Send now” intentionally bypasses pacing, increasing operational risk

**Severity:** High

**Evidence:** `release_now()` moves queued messages to the current time and
`ignore_schedule=True` bypasses the working window and random gap, while caps
remain enforced.

**Impact:** the feature can create a rapid burst of cold email. Daily/hourly
caps reduce account risk but do not provide the same recipient or reputation
protection as normal pacing.

**Recommended fix:** require a stronger confirmation showing exact message count,
estimated duration, and affected accounts. Consider removing the bypass for
live mode or limiting it to a small configurable batch.

### O-11: Claimed SMTP messages are marked sent when delivery is uncertain

**Severity:** High

**Evidence:** `_recover_claimed()` changes `sending` rows to `sent` with an
“outcome unknown, not retried” error after an interruption.

**Impact:** this avoids duplicate mail, which is the safer default, but stats
and lead history now report the message as sent even when Gmail may not have
accepted it. A later campaign will not retry it.

**Recommended fix:** add a distinct `unknown`/`needs_review` status, exclude it
from automatic retry, and show a Sent-folder verification task. Keep the lead
protected from automatic duplication.

## Scraping, Enrichment, and Data Issues

### S-01: Google Maps DOM changes can silently reduce results

**Severity:** High

The parser relies on a mixture of semantic attributes and fragile Google CSS
classes. A selector change can produce incomplete cards while the run still
finishes normally.

**Fix:** detect unusually low card yield, missing required fields, and feed
shape changes; show a degraded-result warning instead of a normal success.

### S-02: Detail extraction failures can look like valid missing data

**Severity:** Medium

Broad failure handling keeps the UI alive but can turn a browser failure into a
row with missing hours, reviews, phone, or website.

**Fix:** preserve a per-record extraction error and expose a task-level warning
with the number of affected records.

### S-03: CSV filenames can be invalid or collide

**Severity:** Medium

Search terms and areas are used to form filenames. Windows-invalid characters,
reserved names, very long names, and repeated searches can cause failures or
overwrite earlier output.

**Fix:** sanitize filenames, cap length, add a timestamp or collision suffix,
and report the final path clearly.

### S-04: Website crawling is inherently untrusted

**Severity:** Medium

Business websites can redirect, return malformed content, use invalid TLS, or
serve compressed/obfuscated HTML. The current code is defensive, but a crawl
failure should remain visibly distinct from “no email exists.”

**Fix:** expose reachable/error/pages-fetched fields in the UI and keep failed
leads available for retry.

## Persistence and Configuration Issues

### P-01: Credential fallback is not secure outside Windows

**Severity:** High outside Windows

The non-Windows fallback uses reversible machine-derived XOR obfuscation. It is
not encryption and should not be described as secure storage.

**Fix:** require a supported OS keyring/secret store, or clearly disable live
sending where secure credential storage is unavailable.

### P-02: AI provider use conflicts with broad local-only messaging

**Severity:** Medium

Website audit digests and business information can be sent to Groq or
OpenRouter when AI personalization is enabled, while some documentation says
everything stays on the computer.

**Fix:** change the product copy to say “scraping and storage are local; AI is
optional and sends audit summaries to the selected provider.” Add a first-use
consent and a provider/offline indicator.

### P-03: Saved-search and documentation contracts are inconsistent

**Severity:** Low

The current implementation supports multiple areas and has current template
behavior that differs from older documentation. The outreach spec still
mentions contracts that are not identical to the current code.

**Fix:** designate one current contract, update `README.md`,
`docs/OUTREACH_SPEC.md`, and `docs/TECHNICAL_REFERENCE.md`, and add contract
tests for every documented public key/signature.

## Naming Change

### Recommended new name: LeadForge

“MapHarvest” describes only the Google Maps collection portion. The product now
also audits websites, creates campaigns, and sends outreach. **LeadForge** is
shorter, describes the complete lead workflow, and avoids tying the brand to a
single source.

The rename must be treated as a coordinated migration, not only a window-title
change. Update these surfaces together:

- Window title and shell header in `ui/app.py`.
- Default export folder in `ui/screen_input.py`.
- Build names and version metadata in `BUILD_EXE.bat`, `main.spec`, and
  `tools/gen_version_file.py`.
- AI provider application name/URL constants in `core/ai.py`.
- Documentation headings, executable names, screenshots, and setup commands.
- User-facing strings and test expectations.

Keep the existing `%USERPROFILE%\\.mapharvest` data directory for one release so
existing settings, templates, databases, caches, and suppression lists are not
lost. Later, migrate to `.leadforge` with a one-time copy and backup. Do not
rename the storage directory silently: deleting or missing the suppression
database can cause opted-out contacts to be targeted again.

## Prepare Campaign Redesign Priority

The most useful fix sequence is:

1. Add Cancel preparation and a clear `preparing/cancelled/failed/ready` state.
2. Make preparation attempts identifiable and clean up or isolate partial queue
   rows.
3. Show a final review panel with queued, skipped, generic, follow-up, account,
   and date totals before enabling sending.
4. Block live sending when an opt-out route is not monitored, unless the user
   explicitly overrides it.
5. Reduce completion-time UI refreshes and preserve the selected campaign.
6. Add tests for cancellation, repeated preparation, partial failures, changed
   settings, and unread IMAP routes.

## Test Gaps

The passing suite does not fully cover:

- Live Google Maps navigation and selector drift.
- Real Gmail SMTP/IMAP behavior.
- Process termination during SMTP hand-off or preparation.
- Repeated Prepare Campaign clicks and partial database cleanup.
- Settings changes while preparation is active.
- Filename collisions and invalid Windows filenames.
- Large-list GUI responsiveness on production-sized databases.
