"""Outreach copy and the Auto Army service catalogue.

Everything a cold email says that does *not* come from the model lives here, so
the copy can be read, reviewed and changed in one place instead of being smeared
across the scheduler and the GUI.

Three ideas hold this module together:

* **The catalogue is the offer.** `AUTO_ARMY_SERVICES` is the real service list,
  written the way the seller writes it. A gap found by `core.audit` is turned
  into service names taken verbatim from that list, so the email never invents a
  capability the business cannot actually deliver.
* **A leaked merge field is the worst bug in this system.** A live cold email
  reading "Hi {{first_name}}" burns the domain and the prospect. `render` never
  emits `{{...}}`: every unresolved token becomes a *gap marker* that is deleted
  along with the punctuation and whitespace that belonged to it, so a missing
  value degrades to a shorter sentence rather than to visible machinery.
* **Copy rules are structural, not advisory.** Under 120 words on a first touch,
  one link, a real sign-off, a subject under 55 characters with no shouting and
  no fake `Re:`. `render` enforces the mechanical ones; `tests/test_templates.py`
  enforces the rest against the shipped templates.

Every template here is a default, not a fixture. `BUILTIN_TEMPLATES` is the
shipped copy and never changes at runtime; whatever the user edits or writes
themselves is one JSON file at `TEMPLATES_PATH`, and `all_templates` lays the two
over each other so an edited built-in takes effect for every caller at once.
Deleting the user's entry brings the original back, so no edit is a one-way door.
`validate_template` reads a template and says what it will cost — a misspelt
merge field, a second link, a subject that shouts — and says it as warnings.
Nothing in this module refuses to send.

Plain text is the source of truth. `to_html` is a deliberately dumb renderer:
system fonts, paragraphs, one anchor, a footer. No images, no tracking, no
tables, nothing that makes a spam filter look twice.
"""

from __future__ import annotations

import hashlib
import html as _html
import json
import os
import re
import urllib.parse
from dataclasses import dataclass
from difflib import get_close_matches

# ── Service catalogue ──

# Verbatim: this is the seller's own wording and it goes into emails unedited.
AUTO_ARMY_SERVICES: dict[str, list[str]] = {
    "Workflow Automation": [
        "automate repetitive business processes",
        "connect apps together",
        "automatic data entry",
        "approvals",
        "notifications",
        "reporting",
    ],
    "AI Automation": [
        "AI customer-support agents",
        "AI email processing",
        "AI document/data extraction",
        "AI lead qualification",
        "AI content generation",
        "AI-powered internal assistants",
        "AI research agents",
        "AI workflow agents",
        "AI decision/triage systems",
        "human-in-the-loop AI systems",
    ],
    "Lead Generation": [
        "business lead discovery",
        "Google Maps lead generation",
        "website lead extraction",
        "LinkedIn/business research",
        "lead enrichment",
        "email finding",
        "company information extraction",
        "lead database building",
    ],
    "Lead Automation": [
        "automatically add leads to CRM",
        "lead qualification",
        "AI lead scoring",
        "lead categorization",
        "automatic follow-ups",
        "email outreach",
        "WhatsApp workflows",
        "appointment booking",
        "sales notifications",
        "CRM pipeline automation",
    ],
    "CRM & Sales Automation": [
        "automatic CRM updates",
        "lead assignment",
        "follow-up reminders",
        "sales pipelines",
        "automated emails",
        "customer onboarding",
    ],
    "Business Process Automation": [
        "HR processes",
        "attendance",
        "payroll workflows",
        "employee onboarding",
        "purchase/order workflows",
        "approval systems",
        "reporting",
    ],
    "Web Scraping & Data Automation": [
        "website data extraction",
        "competitor monitoring",
        "price monitoring",
        "Google Maps/business data",
        "data collection and cleaning",
        "automated reports",
    ],
    "Document Automation": [
        "PDF/document data extraction",
        "invoice processing",
        "receipt processing",
        "contract/document classification",
        "automatic document generation",
    ],
    "Marketing Automation": [
        "social media workflows",
        "email campaigns",
        "content pipelines",
        "SEO automation",
        "customer segmentation",
        "automated reporting",
    ],
}

# The two lines the business most wants to sell. They win ties when a gap maps to
# several services and they are hoisted to the front of any top-up list.
PRIORITY_CATEGORIES: tuple[str, ...] = (
    "Business Process Automation",
    "Web Scraping & Data Automation",
)


def _flatten_catalogue() -> list[str]:
    seen: set[str] = set()
    flat: list[str] = []
    for names in AUTO_ARMY_SERVICES.values():
        for name in names:
            key = name.lower()
            if key not in seen:
                seen.add(key)
                flat.append(name)
    return flat


# Seeded into sender_profile["services"]; catalogue order, de-duplicated
# ("reporting" appears under two categories).
DEFAULT_SERVICES: list[str] = _flatten_catalogue()

# lower() -> the catalogue's own spelling, so "Appointment Booking" typed in the
# GUI still reaches the prospect as "appointment booking".
_CANONICAL: dict[str, str] = {}
# lower() -> every category the name belongs to (a service can sit in two).
_CATEGORIES_OF: dict[str, tuple[str, ...]] = {}
for _cat, _names in AUTO_ARMY_SERVICES.items():
    _CANONICAL.setdefault(_cat.lower(), _cat)
    _CATEGORIES_OF[_cat.lower()] = (_cat,)
    for _name in _names:
        _CANONICAL.setdefault(_name.lower(), _name)
        _CATEGORIES_OF[_name.lower()] = _CATEGORIES_OF.get(_name.lower(), ()) + (_cat,)
del _cat, _names, _name


def canonical_service(name: str) -> str:
    """Catalogue spelling for `name`; unknown names pass through trimmed."""
    key = str(name or "").strip()
    return _CANONICAL.get(key.lower(), key)


def is_priority_service(name: str) -> bool:
    key = str(name or "").strip().lower()
    return any(c in PRIORITY_CATEGORIES for c in _CATEGORIES_OF.get(key, ()))


def top_services(services=(), limit: int = 6) -> list[str]:
    """Rank a service list for the offer line: priority categories first.

    Used for the profile's own list (and by `core.ai` for the six-service offer
    summary it is allowed to send). Order within each band is preserved.
    """
    picked = [canonical_service(s) for s in (services or DEFAULT_SERVICES) if str(s or "").strip()]
    seen: set[str] = set()
    unique = [s for s in picked if not (s.lower() in seen or seen.add(s.lower()))]
    ranked = [s for s in unique if is_priority_service(s)] + [s for s in unique if not is_priority_service(s)]
    return ranked[:limit] if limit > 0 else ranked


# ── Gap → service mapping ──

# Mirrors the catalogue in core.audit and augments it: an audited gap already
# carries its own `services`, and these are appended behind them so the two
# priority lines surface on the gaps where they genuinely apply. Also the sole
# source when a gap dict arrives without services (hand-built leads, CSV import).
#
# Entries only, never the headings they sit under. `service_1` is read inside a
# sentence — "the fix is {{service_1}}" — and a heading dropped into that slot
# sells the reader a section of the seller's own catalogue instead of a thing
# that could be done for them. Where a gap is really the whole line of work, the
# entry beneath it that best answers the gap stands in for the line, and
# `spoken_service` resolves anything that still arrives as a heading.
#
# The other rule this table obeys is that the offer has to follow from the gap.
# The email names one finding and then names what would be built, and a reader
# who cannot draw the line between the two reads a broken mail merge — which is
# worse than an email that said nothing about their site at all. So a code is
# absent here rather than mapped to whatever was nearest: `slow_site` and
# `no_mobile` are website work, the catalogue is not a web shop, and there is no
# honest entry to give them. A gap that reaches `services_for_gaps` with nothing
# on either side of it contributes nothing, and `build_context` will not let it
# become the headline.
GAP_SERVICES: dict[str, list[str]] = {
    "no_online_booking": ["appointment booking", "automatic follow-ups", "sales notifications"],
    "no_live_chat": ["AI customer-support agents", "AI lead qualification",
                     "human-in-the-loop AI systems"],
    "contact_form_only": ["AI lead qualification", "automatic follow-ups",
                          "automatically add leads to CRM"],
    "no_crm_signals": ["automatic CRM updates", "automatically add leads to CRM", "lead assignment"],
    "no_lead_capture": ["business lead discovery", "automatic follow-ups", "website lead extraction"],
    "quote_by_form": ["AI lead scoring", "AI decision/triage systems", "approval systems"],
    "stale_blog": ["AI content generation", "content pipelines"],
    "no_analytics": ["automated reports", "competitor monitoring", "data collection and cleaning"],
    "careers_manual": ["employee onboarding", "AI document/data extraction",
                       "AI decision/triage systems"],
    "ecommerce_manual": ["purchase/order workflows", "approval systems", "invoice processing"],
    "pdf_forms": ["PDF/document data extraction", "automatic document generation",
                  "AI document/data extraction"],
    # Not "reporting" behind "automated reports" — the same pair `core.audit`
    # struck out of no_analytics, one offer said twice and the second word alone
    # reading as a heading. The gap is a message arriving for one branch, so the
    # slots behind it are getting it to that branch.
    "multi_location": ["automated reports", "lead assignment", "sales notifications"],
    "no_social_presence": ["social media workflows", "content pipelines", "customer segmentation"],
    # Nobody has put anything new on the site in years, and what the catalogue
    # sells against that is content that arrives without anyone remembering.
    "stale_site": ["AI content generation", "content pipelines", "SEO automation"],
    # `slow_site` and `no_mobile` are deliberately absent: see the note above.
    "no_schema": ["SEO automation", "competitor monitoring", "website data extraction"],
    "price_opaque": ["AI lead qualification", "price monitoring", "competitor monitoring"],
}


# What to pitch when the audit found nothing to pitch against. Ordered so the
# sentence reads ("the fix is automatic follow-ups") and so the two lines the
# seller cares most about still land in the first three slots — as the work
# under them, which is what the sentence can carry.
DEFAULT_PITCH_SERVICES: list[str] = [
    "automatic follow-ups",
    "approval systems",
    "automated reports",
    "AI lead qualification",
    "appointment booking",
]


# The entry that speaks for a whole line when nothing more specific is known.
# Every one of them is a noun phrase, because the slot they fill is read as the
# object of a verb the template already wrote ("we build...", "the fix is...").
CATEGORY_SERVICE: dict[str, str] = {
    "Workflow Automation": "automatic data entry",
    "AI Automation": "AI workflow agents",
    "Lead Generation": "business lead discovery",
    "Lead Automation": "automatic follow-ups",
    "CRM & Sales Automation": "automatic CRM updates",
    "Business Process Automation": "approval systems",
    "Web Scraping & Data Automation": "automated reports",
    "Document Automation": "automatic document generation",
    "Marketing Automation": "email campaigns",
}


def spoken_service(name: str, code: str = "") -> str:
    """`name` as a prospect can be offered it.

    A catalogue entry passes through in the catalogue's own spelling. A category
    is a heading over entries — the seller's filing, not a deliverable — so a
    heading is spent as the work beneath it: the entry `GAP_SERVICES[code]`
    already chose for this gap, or the line's own stand-in. Unknown names are
    left alone; they are the user's wording and this module does not edit it.

    A line added to the catalogue with no stand-in written for it falls back to
    its first entry rather than raising. Nothing on the send path may fail over
    a copy table, and the first entry is a service either way.
    """
    clean = canonical_service(name)
    if clean not in AUTO_ARMY_SERVICES:
        return clean
    for candidate in GAP_SERVICES.get(str(code or ""), ()):
        if clean in _CATEGORIES_OF.get(candidate.lower(), ()):
            return candidate
    under = AUTO_ARMY_SERVICES[clean]
    return CATEGORY_SERVICE.get(clean) or (under[0] if under else clean)


def services_for_gaps(gaps, extra=()) -> list[str]:
    """Ordered, de-duplicated services to pitch against `gaps` (severity order).

    Each gap contributes its own `services` first, then the `GAP_SERVICES` entry
    for its code. `extra` tops the result up, in the order given, so
    `service_1..3` are always populated even for a thin audit.

    Everything leaves through `spoken_service`, so a gap that names a whole line
    of work — `core.audit` does, and so does a hand-built lead — reaches the copy
    as something that can be built for the reader rather than as a heading.
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(name, code: str = "") -> None:
        clean = spoken_service(name, code)
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            out.append(clean)

    for gap in gaps or []:
        if not isinstance(gap, dict):
            continue
        code = str(gap.get("code") or "")
        for name in gap.get("services") or []:
            _add(name, code)
        for name in GAP_SERVICES.get(code, []):
            _add(name, code)
    for name in extra or ():
        _add(name)
    return out


# ── Templates ──


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    step: int              # 0 = first touch, 1..n = follow-ups
    subject: str
    body: str              # plain text with {{merge_fields}}


SUBJECT_MAX = 55
FIRST_TOUCH_MAX_WORDS = 120

# `city` is not here. It was resolved into every context, with a Google Maps
# scrape and a "your area" fallback behind it, and it appeared in no template:
# the reader never saw it once. Every phrasing that would have used it either
# reads as the mail merge it is or builds a credential out of the reader's own
# listing, which `tests/test_templates.py` refuses outright. A field the
# pipeline pays for and the copy cannot honestly spend is better gone.
MERGE_FIELDS: tuple[str, ...] = (
    "business_name", "first_name", "category", "website_domain",
    "gap_1", "gap_2", "gap_1_evidence", "gap_1_subject",
    "service_1", "service_2", "service_3",
    "ai_subject", "ai_opener", "ai_ps", "sender_name", "sender_title", "company",
    "company_website", "calendar_link", "phone", "postal_address",
    "unsubscribe_line", "proof_point",
)

# Consumed by to_html only; not part of the documented merge-field set.
_EXTRA_CONTEXT_KEYS: tuple[str, ...] = ("unsubscribe_email",)

# What `business_name` reads when the record has no name worth printing. It works
# in the middle of a sentence and identifies nobody, which is why a subject line
# made of nothing but this reaches for the sender's company instead.
_NEUTRAL_NAME = "your business"

# A sentence that lost a merge value is deleted whole (`_drop_holed_sentences`),
# so no sentence may lean on the one before it: no "that step", no "the above", no
# bare "it" pointing back a paragraph. Unreachable sites and leads with no website
# both audit to no gaps at all, and any neighbouring sentence can disappear with
# them. Every sentence has to read on its own.
#
# The mechanical half of that rule: a sentence holding a field that can render
# empty is written *last* on its line, so nothing is left standing behind it when
# it goes. Two sentences that only work together are written as one sentence, and
# then they live or die together. `city`, `category`, `business_name`,
# `first_name` and `website_domain` carry fallbacks and never vanish; everything
# else can.
#
# What the copy may claim is just as fixed. The sender knows what it builds and
# what it charges for fifteen minutes. It does not know the reader's trade, and
# a track record assembled out of the reader's own Google Maps category is a
# fabricated credential. Credibility comes from `proof_point` — the sender's own
# words, from their own profile — or it is left out.
#
# Nor may a template state a fact about the reader's site that the audit did not
# establish. "When someone sends the form on {{website_domain}}" asserted a
# contact form on every lead it went to, including the ones whose headline gap
# was that there is no way to capture a lead at all, the sites that never
# loaded, and the leads with no website. What the audit found arrives as `gap_1`
# and nowhere else; everything the copy wants to know beyond that it has to ask.
BUILTIN_TEMPLATES: list[Template] = [
    Template(
        id="gap_direct",
        name="Headline gap",
        step=0,
        subject="{{gap_1_subject}} at {{business_name}}",
        body=(
            "Hi {{first_name}},\n"
            "\n"
            "{{ai_opener}}\n"
            "\n"
            "One thing stands out on {{website_domain}}: {{gap_1}} ({{gap_1_evidence}}).\n"
            "\n"
            "We build {{service_1}} and {{service_2}} on the tools you run today, and "
            "nothing waits on someone remembering.\n"
            "\n"
            "{{proof_point}}\n"
            "\n"
            "Worth fifteen minutes to see if it fits? {{calendar_link}}\n"
            "\n"
            "{{sender_name}}\n"
            "{{sender_title}}, {{company}}"
        ),
    ),
    # `service_1` is whatever the headline gap resolved to, and that is a
    # different kind of work for each of the eighteen. So nothing around it may
    # describe the work: "the message gets answered, sorted and logged" cohered
    # only while the gap was about an inbox, and read "the fix is automated
    # reports, the message gets answered" for the eleven that were not. What all
    # eighteen share is that somebody is doing it by hand every time, and that is
    # the whole claim. The clause after `service_1` sits inside the same sentence
    # for the reason set out above: on its own it would be a sentence left
    # pointing at a fix that had gone.
    Template(
        id="time_saved",
        name="Hours back",
        step=0,
        subject="the same few minutes, over and over",
        body=(
            "Hi {{first_name}},\n"
            "\n"
            "{{ai_opener}}\n"
            "\n"
            "The work that quietly eats a week is the part somebody redoes by hand "
            "every time: open it, read it, type the same reply, copy it somewhere, "
            "remember to come back to it.\n"
            "\n"
            "The fix is {{service_1}}, running on the tools {{business_name}} already "
            "has rather than on somebody remembering.\n"
            "\n"
            "{{proof_point}}\n"
            "\n"
            "Want to see it on your own setup? {{calendar_link}}\n"
            "\n"
            "{{sender_name}}\n"
            "{{sender_title}}, {{company}}\n"
            "\n"
            "{{ai_ps}}"
        ),
    ),
    Template(
        id="question",
        name="One question",
        step=0,
        subject="how do messages reach {{business_name}}?",
        body=(
            "Hi {{first_name}},\n"
            "\n"
            "{{ai_opener}}\n"
            "\n"
            "When a new enquiry reaches {{business_name}}, where does it land, and who "
            "picks it up?\n"
            "\n"
            "I ask because {{category}} businesses that answer first tend to win the "
            "work, and the wait is usually in who sees a message, not in how fast "
            "anyone types.\n"
            "\n"
            "I read {{website_domain}} before writing, and one line stood out: "
            "{{gap_1}}.\n"
            "\n"
            "If enquiries already land somewhere that chases them, say so and I will "
            "leave it there.\n"
            "\n"
            "{{sender_name}}\n"
            "{{sender_title}}, {{company}}\n"
            "{{company_website}}\n"
            "\n"
            "{{ai_ps}}"
        ),
    ),
    Template(
        id="followup_bump",
        name="Follow-up: bump",
        step=1,
        subject="one more note on {{gap_1_subject}}",
        body=(
            "Hi {{first_name}},\n"
            "\n"
            "Bumping my last email in case it landed in a bad week.\n"
            "\n"
            "Still happy to show what {{service_1}} would look like at {{business_name}}, "
            "or to hear it is not a priority. {{calendar_link}}\n"
            "\n"
            "{{sender_name}}"
        ),
    ),
    Template(
        id="followup_close",
        name="Follow-up: close",
        step=2,
        subject="last one from me",
        body=(
            "Hi {{first_name}},\n"
            "\n"
            "I will close the file here. If there is ever room for {{service_1}}, reply "
            "to this and I will pick it back up.\n"
            "\n"
            "{{sender_name}}"
        ),
    ),
]


# The same list under the name the rest of the app has always used. Whatever
# reads `TEMPLATES` is asking about the shipped copy — the copy rules in
# `tests/test_templates.py`, the follow-up ordering — and not about whatever the
# user has written since. `all_templates` is the one that answers that.
TEMPLATES: list[Template] = BUILTIN_TEMPLATES


# ── User template store ──

# One JSON file beside settings.json. `core.settings` is deliberately not
# imported for the path: it imports this module for `DEFAULT_SERVICES`, the cycle
# is real, and the directory is cheap enough to build the same way it builds
# `SETTINGS_DIR`.
#
# Read as a module global on every call rather than captured, so a test — or a
# portable build that relocates the profile — can point the store somewhere else
# by assigning to it.
TEMPLATES_PATH: str = os.path.join(os.path.expanduser("~"), ".mapharvest", "templates.json")

_STORE_VERSION = 1

# (path, stat stamp, templates). `get_template` is called once per queued
# message, so the alternative is a file open per email. The stamp is the file's
# own mtime and size, which is what makes an edit from outside this process —
# the user opening templates.json in an editor — show up without a restart; a
# write from inside drops the entry outright.
_CACHE: tuple | None = None


def _store_stamp(path: str) -> tuple:
    # Not just OSError: a path carrying a null byte raises ValueError before the
    # filesystem is ever asked, and a store that cannot be located is a store
    # with nothing in it, not a crash on the way to the picker.
    try:
        stat = os.stat(path)
    except Exception:
        return ()
    return (stat.st_mtime_ns, stat.st_size)


def _forget_store() -> None:
    global _CACHE
    _CACHE = None


def _as_dict(tpl: Template) -> dict:
    return {"id": tpl.id, "name": tpl.name, "step": tpl.step,
            "subject": tpl.subject, "body": tpl.body}


def _coerce(data) -> Template | None:
    """One stored entry as a `Template`, or None when there is nothing to read.

    Every field is taken as text whatever the file held. A store the user has
    opened in an editor can put a number in `subject` or a string in `step`, and
    a template that renders a plainer email is a better answer than a load that
    raises. Only a missing id is fatal, because an entry with no id cannot be
    matched to a built-in, reset, or picked.
    """
    if isinstance(data, Template):
        data = _as_dict(data)
    if not isinstance(data, dict):
        return None
    template_id = str(data.get("id") or "").strip()
    if not template_id:
        return None
    try:
        step = int(data.get("step") or 0)
    except (TypeError, ValueError):
        step = 0
    return Template(
        id=template_id,
        # Never blank: a blank name is a blank row in the picker, which is a
        # template the user cannot choose.
        name=str(data.get("name") or "").strip() or template_id,
        step=max(0, step),
        subject=str(data.get("subject") or ""),
        body=str(data.get("body") or ""),
    )


def _read_store(path: str) -> list[Template]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return []
    # A bare list is what a hand-written file usually is; the app writes the
    # versioned object.
    entries = data.get("templates") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return []
    out = []
    for entry in entries:
        tpl = _coerce(entry)
        if tpl is not None:
            out.append(tpl)
    return out


def _write_store(templates) -> bool:
    """Replace the store with `templates`, atomically. False when it could not.

    Temp file then `os.replace`, for the reason `core.settings` does it: a crash
    or a full disk halfway through a write would otherwise cost the user every
    template they have ever edited, and the file they lose is the one thing here
    that cannot be regenerated.
    """
    path = TEMPLATES_PATH
    tmp = path + ".tmp"
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        payload = {"version": _STORE_VERSION,
                   "templates": [_as_dict(t) for t in templates]}
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        return False
    finally:
        _forget_store()
    return True


def load_user_templates() -> list[Template]:
    """Every template the user has saved. [] when there is no readable store.

    A file that was hand-edited into invalid JSON, truncated by a crash or
    written by something else costs the user their edits and nothing else: the
    five built-ins are still there, the app still sends, and the next save writes
    a whole valid file over the top of it.
    """
    global _CACHE
    path = TEMPLATES_PATH
    stamp = _store_stamp(path)
    if _CACHE is not None and _CACHE[0] == path and _CACHE[1] == stamp:
        return list(_CACHE[2])
    try:
        loaded = tuple(_read_store(path))
    except Exception:
        loaded = ()
    _CACHE = (path, stamp, loaded)
    return list(loaded)


def all_templates() -> list[Template]:
    """The built-ins with the user's edits laid over them, then the user's own.

    A stored entry whose id matches a built-in replaces it outright — for every
    caller at once, because this is what `get_template` and `templates_for_step`
    read — and `BUILTIN_TEMPLATES` is untouched underneath, which is what lets
    `reset_template` put the original back.

    Order is stable: step, then the shipped order within a step, then name. A
    picker built from this does not reshuffle itself when a template is edited.
    """
    try:
        order = {tpl.id: index for index, tpl in enumerate(BUILTIN_TEMPLATES)}
        merged = {tpl.id: tpl for tpl in BUILTIN_TEMPLATES}
        for tpl in load_user_templates():
            merged[tpl.id] = tpl
        return sorted(merged.values(),
                      key=lambda t: (t.step, order.get(t.id, len(order)),
                                     t.name.lower(), t.id))
    except Exception:
        return list(BUILTIN_TEMPLATES)


def get_template(template_id: str) -> Template | None:
    for tpl in all_templates():
        if tpl.id == template_id:
            return tpl
    return None


def templates_for_step(step: int) -> list[Template]:
    return [t for t in all_templates() if t.step == step]


def is_builtin(template_id: str) -> bool:
    """Does this id belong to one of the shipped five?"""
    return any(tpl.id == template_id for tpl in BUILTIN_TEMPLATES)


def is_overridden(template_id: str) -> bool:
    """Is a shipped template currently being replaced by a user edit?

    False for a template the user wrote themselves: there is no original under
    it, so there is nothing to reset it to and the editor offers delete instead.
    """
    return is_builtin(template_id) and any(tpl.id == template_id
                                           for tpl in load_user_templates())


def save_user_template(tpl: Template) -> None:
    """Write `tpl` to the store, replacing any entry carrying the same id.

    Saving a built-in's id writes an override; the shipped copy is never touched,
    so `reset_template` can always undo it. A store that cannot be written — a
    read-only home directory, a full disk — leaves the previous file exactly as
    it was, and a caller that needs to know reads `get_template` back.
    """
    try:
        entry = _coerce(tpl)
        if entry is None:
            return
        kept = [t for t in load_user_templates() if t.id != entry.id]
        kept.append(entry)
        _write_store(kept)
    except Exception:
        return


def delete_user_template(template_id: str) -> bool:
    """Drop the user's entry for `template_id`. True when there was one.

    For a built-in id this restores the shipped copy. For a template the user
    wrote it removes it from every picker, and nothing comes back.
    """
    wanted = str(template_id or "").strip()
    stored = load_user_templates()
    kept = [t for t in stored if t.id != wanted]
    if len(kept) == len(stored):
        return False
    return _write_store(kept)


def reset_template(template_id: str) -> Template | None:
    """Drop the override for `template_id` and hand back what it reverts to.

    The shipped built-in for a built-in id; None for a template the user wrote
    themselves, which this deletes, because there is nothing underneath it.
    """
    delete_user_template(template_id)
    return get_template(template_id)


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def new_template_id(name: str, step: int) -> str:
    """An id for a new template: readable, stable, and not already taken.

    Derived from the name so the stored file stays legible to anyone who opens
    it, and checked against the built-ins as well as the store so a new template
    can never silently become an override of one.
    """
    try:
        step = max(0, int(step))
    except (TypeError, ValueError):
        step = 0
    try:
        base = _SLUG_RE.sub("_", str(name or "").lower()).strip("_")[:40].strip("_")
    except Exception:
        base = ""
    base = base or ("custom" if step == 0 else "custom_step%d" % step)
    taken = {t.id for t in BUILTIN_TEMPLATES} | {t.id for t in load_user_templates()}
    if base not in taken:
        return base
    for suffix in range(2, 1000):
        candidate = "%s_%d" % (base, suffix)
        if candidate not in taken:
            return candidate
    return "%s_%s" % (base, hashlib.sha1(base.encode("utf-8", "replace")).hexdigest()[:8])


# ── Rendering ──

# A sentinel standing where a merge field resolved to nothing. Keeping the hole
# visible through the tidy pass is what lets punctuation that belonged to the
# missing value disappear with it, instead of leaving "Hi ," or "out: ." behind.
_GAP = "\x00"

_FIELD_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
_TOKEN_RE = re.compile(r"\{\{[^{}]*\}\}")
_SINGLE_BRACE_RE = re.compile(r"\{(?:%s)\}" % "|".join(MERGE_FIELDS + _EXTRA_CONTEXT_KEYS))
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
# A colon and nothing else. Treating a hyphen as a delimiter too made "Re-Max
# Realty" a reply to "Max Realty", and a lead whose audit found no gaps reaches
# the subject line as its business name and nothing else. Mirrored in
# `core.mailer._plain_subject`, which holds the same guarantee at the wire.
_SUBJECT_PREFIX_RE = re.compile(r"^\s*(?:re|fw|fwd)\s*:\s*", re.I)
# A salutation the model wrote for itself: the greeting word, an optional name or
# two behind it, and the punctuation that closes it. The closing punctuation is
# required, so "Hidden costs" and "Hire us in March" are left alone.
_GREETING_RE = re.compile(
    r"^\s*(?:hi|hey|hello|dear|greetings|good\s+(?:morning|afternoon|evening))\b"
    r"(?:\s+(?:mr|mrs|ms|dr)\.?)?"
    r"(?:\s+(?:[A-Za-z][A-Za-z'\-]{0,19}|%s)){0,2}\s*[,;:.]\s*" % _GAP, re.I)
_DASHES = {"—": ", ", "–": "-", "‒": "-", "―": ", ", "--": ", "}

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_HOLE_AFTER_WORD_RE = re.compile(r"[A-Za-z0-9][^%s]*%s" % (_GAP, _GAP))
_EMPTY_PAREN_RE = re.compile(r"[ \t]*\(\s*%s\s*\)" % _GAP)
# Words a line can end on only because the value behind them vanished: they point
# forward at something and a subject that stops here points at nothing.
_DANGLING = r"at|on|for|to|with|about|from|in|into|by|as|and|or|but|of|the|a|an|your|my"
# Characters that join two things. Whatever the reason one side is missing — a cut
# at the character cap, a merge field that resolved to nothing — a joiner left at
# the end is machinery showing ("Coastal Fabrication &").
_JOINERS = " \t,;:.\\-&+/|"


def _trim_tail(text: str) -> str:
    """Strip trailing joiners and forward-pointing words, however many deep.

    One pass is not enough: "Coastal Fabrication & Welding" cut at the cap leaves
    "Coastal Fabrication &", and "reporting and onboarding and" leaves a word and
    then a comma behind it.
    """
    while True:
        trimmed = re.sub(r"(?:^|\s)(?:%s)$" % _DANGLING, "", text.rstrip(_JOINERS), flags=re.I)
        if trimmed == text:
            return text
        text = trimmed


def _resolve(text: str, ctx: dict) -> str:
    """Substitute merge fields; anything unresolved becomes a gap marker."""

    def _sub(m: re.Match) -> str:
        value = ctx.get(m.group(1), "")
        value = "" if value is None else str(value).strip()
        return value or _GAP

    out = _FIELD_RE.sub(_sub, text)
    # Values may themselves contain braces (a model echoing a placeholder, a
    # business literally named "{{}}"). Sweep until nothing brace-shaped is left.
    for _ in range(3):
        swept = _SINGLE_BRACE_RE.sub(_GAP, _TOKEN_RE.sub(_GAP, out))
        if swept == out:
            break
        out = swept
    return out.replace("{{", "").replace("}}", "")


def _drop_holed_sentences(line: str) -> str:
    """Delete whole sentences that lost a value from their middle.

    "I ask because {{gap_1}} is usually what sits behind a slow reply" with no
    gap reads "I ask because is usually" — worse than silence. A sentence is
    only dropped when the hole follows real words *and* the sentence is
    terminated, which spares greetings, signature lines and trailing links.
    """
    if _GAP not in line:
        return line
    kept = [
        piece for piece in _SENTENCE_SPLIT_RE.split(line)
        if not (_GAP in piece
                and _HOLE_AFTER_WORD_RE.search(piece)
                and re.search(r"[.!?][\"')\]]?\s*$", piece))
    ]
    return " ".join(kept)


def _tidy_line(line: str, drop_holed: bool = True) -> str:
    line = _EMPTY_PAREN_RE.sub("", line)                   # parenthetical whose value vanished
    if drop_holed:
        line = _drop_holed_sentences(line)
    # A joiner that was holding the line's last value on: "{{sender_title}},
    # {{company}}" with the company cleared signs the email "Automation lead,".
    # `_trim_tail` does this for subjects; a body line needs it too, and both are
    # anchored to the hole so a line that resolved keeps its punctuation.
    line = re.sub(r"[ \t]*[,;:&+/|\-]+[ \t]*(%s[ \t]*)$" % _GAP, r"\1", line)
    line = re.sub(r"([ \t]*)%s([ \t]*)" % _GAP,
                  lambda m: " " if (m.group(1) or m.group(2)) else "", line)
    line = re.sub(r"\(\s*\)|\[\s*\]", "", line)
    line = re.sub(r"[ \t]+", " ", line)
    line = re.sub(r"\s+([,.;:!?])", r"\1", line)
    line = re.sub(r"([,;:])\s*([.!?])", r"\2", line)        # "out: ." -> "out."
    line = re.sub(r"[.,;:]\s*\)\s*\.", ").", line)          # "(evidence.)." -> "(evidence)."
    line = re.sub(r"([,;:])\1+", r"\1", line)
    line = re.sub(r"\(\s+", "(", line)
    line = re.sub(r"\s+\)", ")", line)
    line = re.sub(r"^[\s,;:.]+", "", line)
    return line.replace(_GAP, "").strip()


def _tidy(text: str) -> str:
    lines = []
    for raw in text.splitlines():
        line = _tidy_line(raw)
        # A line that collapsed to punctuation carries no meaning; a line that was
        # blank to begin with is paragraph structure and must survive.
        if line or not raw.strip():
            lines.append(line)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _subject_rules(raw: str) -> str:
    """Every mechanical subject rule, in one place so no path can skip them.

    Whatever the subject is made of — the template, the model, or a fallback
    reached because both came to nothing — it arrives at the reader having been
    through the same tidy, the same `Re:` strip, the same de-shouting and the
    same tail trim.
    """
    subject = _tidy_line(raw, drop_holed=False)
    # "Re:"/"Fwd:" on a first contact is a lie about the thread. Strip it wherever
    # it comes from, including from the model.
    while _SUBJECT_PREFIX_RE.match(subject):
        subject = _SUBJECT_PREFIX_RE.sub("", subject, count=1)
    subject = subject.replace("!", "")
    # Shouting is a spam signal; a business name in caps is the usual source.
    subject = " ".join(
        w.capitalize() if len(w) >= 5 and w.isupper() else w
        for w in subject.split(" ")
    )
    return _trim_tail(re.sub(r"\s{2,}", " ", subject).strip())


def _clean_subject(subject: str, ctx: dict) -> str:
    raw = subject.replace("\n", " ")
    # "one more note on {{gap_1}}" with no gap must not end on "on". Both rules
    # are anchored to the hole, so a subject that resolved fully keeps every word.
    raw = re.sub(r"(?:^|\s)(?:%s)\s*%s" % (_DANGLING, _GAP), _GAP, raw, flags=re.I)
    raw = re.sub(r"%s\s*(?:%s)(?:\s|$)" % (_GAP, _DANGLING), _GAP, raw, flags=re.I)
    subject = _subject_rules(raw)
    # The fallbacks are values, not templates: nothing has resolved them and
    # nothing has cleaned them. A business named "{{name}}" put the machinery
    # straight into the subject line, and one whose name is all punctuation put
    # nothing there at all and became a lead skipped without a reason.
    if subject.lower() == _NEUTRAL_NAME or not re.search(r"[A-Za-z0-9]", subject):
        for fallback in (ctx.get("business_name"), ctx.get("company")):
            name = str(fallback or "").strip()
            if not name or name.lower() == _NEUTRAL_NAME:
                continue
            subject = _subject_rules(_resolve(name, ctx))
            if re.search(r"[A-Za-z0-9]", subject):
                break
    if len(subject) > SUBJECT_MAX:
        subject = subject[:SUBJECT_MAX].rsplit(" ", 1)[0]
    return _trim_tail(subject)


def _strip_dashes(text: str) -> str:
    for dash, repl in _DASHES.items():
        text = text.replace(dash, repl)
    return text


def render(template: Template, ctx: dict) -> tuple[str, str, str]:
    """Render a template against `ctx`.

    Returns `(subject, body_text, body_html)`. `body_text` carries the compliance
    footer; `body_html` is the same content with the footer as a grey block.

    A non-empty `ctx["ai_subject"]` replaces the template subject on a first
    touch (step 0) only. Follow-ups keep their own subject: the model never sees
    the earlier thread, so it cannot write a line that follows on from it.

    Never raises, and never emits `{{...}}`.
    """
    ctx = dict(ctx or {})
    try:
        source = template.subject
        ai_subject = str(ctx.get("ai_subject") or "").strip()
        if template.step == 0 and ai_subject:
            source = ai_subject
        subject = _clean_subject(_resolve(source, ctx), ctx)

        core = _tidy(_resolve(template.body, ctx))
        body_html = _body_html(core, ctx)
        footer = _footer_text(ctx)
        body_text = core + "\n\n" + footer if footer else core
        return subject, body_text, body_html
    except Exception:
        return "", "", ""


def _footer_text(ctx: dict) -> str:
    identity = " | ".join(
        p for p in (str(ctx.get("company") or "").strip(),
                    str(ctx.get("postal_address") or "").strip()) if p
    )
    unsubscribe = str(ctx.get("unsubscribe_line") or "").strip()
    return "\n".join(p for p in (identity, unsubscribe) if p)


def _linkify(escaped: str) -> str:
    return _URL_RE.sub(
        lambda m: '<a href="%s" style="color:#1a56db;">%s</a>' % (m.group(0), m.group(0)),
        escaped,
    )


def _footer_html(ctx: dict) -> str:
    identity = " | ".join(
        _html.escape(p) for p in (str(ctx.get("company") or "").strip(),
                                  str(ctx.get("postal_address") or "").strip()) if p
    )
    line = _html.escape(str(ctx.get("unsubscribe_line") or "").strip())
    address = str(ctx.get("unsubscribe_email") or "").strip()
    if line and address:
        href = "mailto:%s?subject=unsubscribe" % urllib.parse.quote(address, safe="@")
        line = re.sub(
            r"unsubscribe",
            lambda m: '<a href="%s" style="color:#8a8a8a;">%s</a>' % (href, m.group(0)),
            line, count=1, flags=re.I,
        )
    parts = [p for p in (identity, line) if p]
    if not parts:
        return ""
    return (
        '<hr style="border:none;border-top:1px solid #e5e5e5;margin:22px 0 12px 0;">'
        '<p style="margin:0;font-size:12px;line-height:1.5;color:#8a8a8a;">%s</p>'
        % "<br>".join(parts)
    )


def _body_html(core: str, ctx: dict) -> str:
    """`core` — a body with no footer in it — as paragraphs, plus the footer.

    Every paragraph it is handed is printed. Nothing here reads a block's text
    to decide what the block is, so whatever the plain-text part says the HTML
    part says too: the two MIME alternatives cannot drift apart on a profile
    whose sign-off happens to read like the footer.
    """
    blocks = []
    for para in re.split(r"\n\s*\n", str(core or "").strip()):
        lines = [ln.strip() for ln in para.splitlines() if ln.strip()]
        if not lines:
            continue
        blocks.append(
            '<p style="margin:0 0 14px 0;">%s</p>'
            % "<br>".join(_linkify(_html.escape(ln)) for ln in lines)
        )
    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\','
        "Roboto,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.55;"
        'color:#1a1a1a;">%s%s</div>' % ("".join(blocks), _footer_html(ctx))
    )


def to_html(body_text: str, ctx: dict) -> str:
    """Deliverability-safe HTML for `body_text`.

    Pass the body *without* a footer: the footer is rendered from `ctx`. A body
    that already carries the plain-text footer is accepted and de-duplicated, so
    a caller that hands back `render`'s `body_text` still gets one footer.
    """
    ctx = dict(ctx or {})
    try:
        body = str(body_text or "").strip()
        # Cut as the exact suffix `render` appended, once, off the end. The old
        # rule recognised the footer by its wording anywhere in the body, and the
        # identity line is the company on its own whenever no postal address is
        # configured — which is also how a sender with no name and no title signs
        # off. That signature was deleted from the HTML part while the plain-text
        # part kept it, so the two alternatives went out carrying different copy,
        # and only on the incomplete profile the app now invites a send from.
        footer = _footer_text(ctx)
        if footer:
            body = re.sub(r"(?:\A|\n\s*\n)%s\Z" % re.escape(footer), "", body).rstrip()
        return _body_html(body, ctx)
    except Exception:
        return ""


# ── Template validation ──

# Wording a filter looks twice at. Advisory, and only advisory: nothing on this
# list is blocked, rewritten or refused, and a phrase here inside a sentence that
# earns it is fine. It exists so the editor can say what a line costs before the
# reply rate says it. A real filter scores dozens of signals together and weighs
# them against the sender's own history; no word list is that, and this one is
# not pretending to be.
SPAM_TRIGGER_WORDS: tuple[str, ...] = (
    "100% free", "act now", "amazing", "apply now", "best price", "buy now",
    "call now", "cash bonus", "cheap", "click here", "congratulations",
    "dear friend", "discount", "double your", "earn extra cash", "extra income",
    "financial freedom", "for free", "free access", "free gift", "free money",
    "free offer", "free trial", "get paid", "guarantee", "guaranteed",
    "increase sales", "instant access", "limited time", "lowest price",
    "make money", "miracle", "money back", "no catch", "no credit check",
    "no obligation", "no strings attached", "offer expires", "once in a lifetime",
    "one time offer", "order now", "risk free", "risk-free",
    "satisfaction guaranteed", "special promotion", "this is not spam", "urgent",
    "while supplies last", "winner", "you have been selected",
)

# The shorteners that hide where a link goes, which is exactly why filters score
# them. Advisory in the same way, and incomplete for the same reason.
LINK_SHORTENERS: tuple[str, ...] = (
    "bit.ly", "buff.ly", "cutt.ly", "goo.gl", "is.gd", "lnkd.in", "ow.ly",
    "rb.gy", "rebrand.ly", "shorturl.at", "t.co", "tiny.cc", "tinyurl.com",
)

# The two merge fields that resolve to a URL. Counted as links even though the
# template only holds a token, because a link is what the reader receives.
_LINK_FIELDS: tuple[str, ...] = ("calendar_link", "company_website")

# Four letters, not three: CRM, PDF, HR and AI are how the trade writes itself
# down, and flagging those would teach the user to ignore the whole panel.
_SHOUT_RE = re.compile(r"\b[A-Z]{4,}\b")


def _term_re(term: str) -> re.Pattern:
    """A phrase as a whole-word pattern, tolerant of the spacing around it."""
    inner = r"\s+".join(re.escape(word) for word in term.split())
    head = r"(?<![\w-])" if term[:1].isalnum() else ""
    tail = r"(?![\w-])" if term[-1:].isalnum() else ""
    return re.compile(head + inner + tail, re.I)


_SPAM_RES = tuple((term, _term_re(term)) for term in SPAM_TRIGGER_WORDS)
# "t.co" sits inside "client.com" and "bit.ly" inside "orbit.ly", so the host has
# to be bounded on both sides or every second template reads as a shortener.
_SHORTENER_RES = tuple(
    (host, re.compile(r"(?<![\w-])%s(?![\w-])" % re.escape(host), re.I))
    for host in LINK_SHORTENERS)


def _finding(level: str, field: str, message: str) -> dict:
    return {"level": level, "field": field, "message": message}


def _raw_field(tpl, key: str) -> str:
    """A field as the editor holds it, before `_coerce` fills anything in."""
    if isinstance(tpl, dict):
        return str(tpl.get(key) or "")
    return str(getattr(tpl, key, "") or "")


def _closest_field(name: str) -> str:
    """The merge field `name` was probably meant to be, or ""."""
    key = re.sub(r"[^a-z0-9_]+", "_", str(name or "").lower()).strip("_")
    if key in MERGE_FIELDS:
        return key
    close = get_close_matches(key, MERGE_FIELDS, n=1, cutoff=0.6)
    return close[0] if close else ""


def _token_findings(text: str, field: str) -> list[dict]:
    """Merge fields that are not merge fields.

    A typo is the single easiest way to ship a broken email, because it fails
    silently: `{{buisness_name}}` resolves to nothing, and the sentence holding
    it is deleted with it, so the message that goes out is simply shorter than
    the one that was written and nothing anywhere says so.
    """
    out = []
    seen = set()
    for token in _TOKEN_RE.findall(text):
        match = _FIELD_RE.fullmatch(token)
        name = match.group(1) if match else token[2:-2].strip()
        # One line per typo, however many times it was written: the same
        # sentence three times over is a panel the user stops reading.
        if name in MERGE_FIELDS or name in seen:
            continue
        seen.add(name)
        guess = _closest_field(name)
        if guess:
            message = ("%s is not a merge field. Did you mean {{%s}}? As written "
                       "it renders as nothing and takes its sentence with it."
                       % (token, guess))
        else:
            message = ("%s is not a merge field, so it renders as nothing and the "
                       "sentence holding it is dropped." % token)
        out.append(_finding("error", field, message))
    return out


def _brace_findings(text: str, field: str) -> list[dict]:
    if text.count("{{") != text.count("}}"):
        return [_finding("error", field,
                         "Unbalanced braces. A {{ with no }} behind it prints the "
                         "field name to the reader as plain words.")]
    stray = _TOKEN_RE.sub("", text)
    if "{" in stray or "}" in stray:
        return [_finding("error", field,
                         "A single brace is not a merge field. They need two: "
                         "{{business_name}}, not {business_name}.")]
    return []


def _shouting_findings(text: str, field: str) -> list[dict]:
    out = []
    shouted = sorted({word for word in _SHOUT_RE.findall(text)})
    if shouted and field == "subject":
        out.append(_finding("warning", field,
                            "Caps in a subject read as shouting: %s. Words of five "
                            "letters or more are put back into sentence case before "
                            "sending; shorter ones go out as typed."
                            % ", ".join(shouted[:4])))
    elif shouted:
        out.append(_finding("warning", field,
                            "Caps read as shouting and score against you at the "
                            "filter: %s." % ", ".join(shouted[:4])))
    if "!" in text and field == "subject":
        out.append(_finding("warning", field,
                            "Exclamation marks in a subject are an old filter "
                            "signal. They are stripped before sending."))
    elif "!" in text:
        out.append(_finding("warning", field,
                            "%d exclamation mark(s). In a cold email they read as a "
                            "stranger shouting in the first line." % text.count("!")))
    return out


def _spam_findings(text: str, field: str) -> list[dict]:
    out = []
    hits = [term for term, pattern in _SPAM_RES if pattern.search(text)]
    if hits:
        named = ", ".join(hits[:4]) + (", and more" if len(hits) > 4 else "")
        out.append(_finding("warning", field,
                            "Wording filters score: %s. Nothing is blocked and "
                            "nothing is rewritten; it costs inbox placement, not "
                            "the send." % named))
    shortened = [host for host, pattern in _SHORTENER_RES if pattern.search(text)]
    if shortened:
        out.append(_finding("warning", field,
                            "A link shortener (%s) hides where the link goes, which "
                            "is why filters treat one in a cold email as a hidden "
                            "destination. Use the real URL."
                            % ", ".join(shortened[:3])))
    return out


def validate_template(tpl, ctx: dict | None = None) -> list[dict]:
    """Everything worth knowing about a template before it is sent, as warnings.

    Returns a list of `{"level": "error"|"warning", "field": "subject"|"body"|
    "name", "message": str}`. It never blocks, never rewrites and never refuses:
    "error" means the reader will see something broken, "warning" means it will
    cost deliverability, and both are the user's call. An empty list means
    nothing was found, not that the copy is good.

    `ctx` is optional and is a `build_context` result. Given one, the two
    compliance lines are checked against what the footer will actually carry
    rather than against the template text — `render` writes that footer from the
    sender profile, not from the body, so a body without them is normal and a
    profile without them is not.
    """
    findings: list[dict] = []
    try:
        entry = _coerce(tpl)
        if entry is None:
            return [_finding("error", "name",
                             "This template has no id, so it cannot be saved, "
                             "picked or reset.")]

        name = _raw_field(tpl, "name").strip()
        if not name:
            findings.append(_finding("warning", "name",
                                     "No name. The picker shows this as %s, which "
                                     "is the id and not a description." % entry.id))
        else:
            clash = [t.name for t in all_templates()
                     if t.id != entry.id and t.name.strip().lower() == name.lower()]
            if clash:
                findings.append(_finding("warning", "name",
                                         "Another template is also called %s, so the "
                                         "picker shows two rows that read the same."
                                         % name))

        subject = _raw_field(tpl, "subject")
        findings.extend(_token_findings(subject, "subject"))
        findings.extend(_brace_findings(subject, "subject"))
        if not subject.strip():
            findings.append(_finding("warning", "subject",
                                     "No subject. The message falls back to the "
                                     "business name on its own, which reads as a "
                                     "mail merge."))
        elif len(subject) > SUBJECT_MAX:
            findings.append(_finding("warning", "subject",
                                     "%d characters as written. Anything over %d is "
                                     "cut at the last whole word before sending, and "
                                     "phones show less than that."
                                     % (len(subject), SUBJECT_MAX)))
        if _SUBJECT_PREFIX_RE.match(subject):
            findings.append(_finding("warning", "subject",
                                     "A Re:/Fwd: prefix claims a reply to a message "
                                     "that was never sent. It is stripped before "
                                     "sending: readers who notice report the sender, "
                                     "and that is the report that ends a domain."))
        findings.extend(_shouting_findings(subject, "subject"))
        findings.extend(_spam_findings(subject, "subject"))

        body = _raw_field(tpl, "body")
        findings.extend(_token_findings(body, "body"))
        findings.extend(_brace_findings(body, "body"))
        if not body.strip():
            findings.append(_finding("error", "body",
                                     "Empty body. The message goes out as a subject "
                                     "line and a footer."))
        used = _FIELD_RE.findall(body)
        words = len(_TOKEN_RE.sub("x", body).split())
        if entry.step == 0 and words > FIRST_TOUCH_MAX_WORDS:
            findings.append(_finding("warning", "body",
                                     "%d words before the merge fields are filled, "
                                     "and they only add. A first touch over %d reads "
                                     "as a pitch and gets skimmed."
                                     % (words, FIRST_TOUCH_MAX_WORDS)))
        links = len(_URL_RE.findall(body)) + sum(1 for f in used if f in _LINK_FIELDS)
        if links > 1:
            findings.append(_finding("warning", "body",
                                     "%d links. Two or more in a first email is a "
                                     "filter signal; the rule the shipped copy keeps "
                                     "is one, the calendar or the site." % links))
        findings.extend(_shouting_findings(body, "body"))
        findings.extend(_spam_findings(body, "body"))

        if entry.step == 0:
            inline = [f for f in ("unsubscribe_line", "postal_address") if f in used]
            if inline:
                findings.append(_finding("warning", "body",
                                         "Every message already carries the "
                                         "unsubscribe line and the postal address in "
                                         "its footer, from your sender profile, so "
                                         "%s here sends them twice."
                                         % ", ".join("{{%s}}" % f for f in inline)))
            for field, label in (("unsubscribe_line", "way to unsubscribe"),
                                 ("postal_address", "postal address")):
                if ctx is None or field in used:
                    continue
                if str(ctx.get(field) or "").strip():
                    continue
                findings.append(_finding("warning", "body",
                                         "This sends with no %s in it. That line and "
                                         "that address are most of what keeps cold "
                                         "mail out of the spam folder; fill it in on "
                                         "the Settings screen." % label))
    except Exception:
        return findings
    return findings


# ── Context ──

# Given names, as a mailbox spells them. Deliberately a positive test: a list of
# departments cannot be finished, because every trade and every language invents
# its own (frontdesk, estimating, surgery, workshop, wartung, atelier), and the
# one it invents next greets a filing cabinet by name. This list cannot be
# finished either, but its unknowns cost a "Hi there," and nothing else.
#
# Kept to names that are not also a function a small business hands an address
# to: no "bill", no "art", no "may". A person called Bill still gets "Hi there,"
# and still gets his email.
_GIVEN_NAMES: frozenset[str] = frozenset("""
aaron abdul abigail adam adrian agnes ahmad ahmed aisha alain alan albert alberto alec
alejandro aleksander alessandro alex alexander alexandra alexandre alfie ali alice
alison amanda amelia amina amir amit amy ana anand anders andre andrea andreas andres
andrew andrzej andy angela angelo angus anil anita anjali ann anna anne annette annie
anthony antoine antonio anup archie ariel arjun armando arnaud arthur artur arun asha
ashley ashok aurelie austin ava avi ayesha aziz barbara barry bart beata beatrice
belinda ben benjamin bernard bernd beth bethany bettina beverley bianca birgit bjorn
blake bogdan bonnie brandon brenda brendan brett brian bridget bruce bruno bryan callum
cameron camille carl carla carlo carlos carmen carol carole caroline carrie catherine
cathy cecile cesar charles charlie charlotte chelsea cheryl chloe chris christian
christina christine christoph christopher cindy claire clara clare claude claudia
claudio clive colin colleen connie connor conor constance corinne craig cristina curtis
cynthia daisy dale damian damien dan dana daniel daniele danielle danny daria dariusz
darren dave david davide dawn dean deborah declan deepak delphine denise dennis derek
desmond dev diana diane diego dieter dimitri dirk divya dolores dominic dominique
donald donna dora doreen doris dorota dorothy douglas duncan dylan eamon eddie edith
eduardo edward eileen eitan elaine eleanor elena eleni eli elias elisa elise elizabeth
ella ellen ellie elodie eloise emeka emilia emilie emilio emily emma enrico enrique
eric erica erik erin ernest ernesto esther etienne eugene eva evan evelyn ewa ewan
fabien fabio faith farhan fatima federico felicity felipe felix fernando filip filippo
finn fiona florence florian frances francesca francis francisco francois frank franz
fraser fred frederic frederick freya gabriel gabriele gail gareth gary gavin gemma
geoff geoffrey george georgia georgina gerald gerard gianluca gilbert gilles gillian
gina giorgio giovanni giulia giuseppe glen glenn gloria gordon grace graham grant greg
gregory grzegorz guillaume gunnar gurpreet gustavo guy hamza hana hannah hans harold
harpreet harriet harry harvey hassan hayley hazel heather hector heidi heiko helen
helena helga henri henrik henry hilary holger holly howard hugh hugo hussein iain ian
ibrahim ida ignacio ilan imogen imran indira ines ingrid ioannis irena irene iris isaac
isabel isabella isabelle isla ismail israa ivan ivy jacek jack jacob jacqueline jade
jaime jake james jamie jan jane janet janice jared jasmine jason jasper javier jay jean
jeanne jeff jeffrey jennifer jenny jens jeremy jerome jerry jesse jessica jill jim
joachim joan joanna joanne joel johan johannes john johnny jon jonas jonathan jordan
jorge jose joseph josh joshua josephine joy joyce jozef juan judith judy julia julian
julie julien julio justin justyna kai kamal kamil karan karen karim karin karl
karolina kate katerina katherine kathleen kathryn kathy katie katrin katrina kavita kay
keith kelly ken kenneth kerry kevin khalid kieran kim kiran kirsten kirsty klaus krista
kristen krishna krzysztof kumar kwame kyle kylie lance lars laura lauren laurence
laurent lawrence leah leanne lee lei lena leo leonard leonardo lesley leslie lewis liam
lilian lily linda lindsay ling lisa lloyd logan lois lorenzo lorna lorraine louis
louise luc luca lucas lucia lucie lucy luigi luis luke lukas lydia lynn maciej
madeleine magdalena magnus mahmoud malcolm malgorzata malik mandy manoj manuel manon
marc marcel marcin marco marcus marek margaret maria mariam marian marianne marie
marijke mario marion marius mark marko markus marta martha martin martina marvin mary
mason mateo mateusz mathieu matt matteo matthew matthias maureen maurice max maxime
maxwell megan mei melanie melissa mette michael michal micheal michel michele michelle
miguel mikael mike milena miles millie milos min miranda miriam mitchell moira
mohamed mohammed molly monica monika morgan moshe muhammad murray mustafa nadia
naomi natalia natalie natasha nathalie nathan naveen neha neil nelson niall nicholas
nick nicola nicolas nicole nigel nikhil nikos nils nina nisha noah noel noemie nora
norbert norman nour olaf ole oleg olga oliver olivia olivier ollie omar oscar oskar
osman owen pablo paige pamela paola paolo pascal patricia patrick paul paula pauline
pawel pedro peggy penelope penny per peter petra phoebe phyllis pierre pieter pilar
piotr polly pooja prakash pranav priya rachel radek rafael rafal rahul raj rajesh
rakesh ralf ralph ramesh ramon rania raphael rashid rasmus raul ravi raymond rebecca
reece regina rehan rekha remi rene renata rhys ricardo richard rick rita ritu robert
roberta roberto robin robyn rodrigo roger rohan rohit roland rolf roman ron ronald
rory rosa rose rosemary ross rowena roy ruben ruby rudolf rupert russell ruth ruud
ryan sabine sabrina sachin sadia saeed sally salvatore sam samantha sameer samir
samuel sandeep sandra sandrine sanjay sanne santiago sara sarah sean sebastian
sebastien selina serena serge sergio seth severine shane shannon sharon shaun shawn
sheila shelley shirley shreya sian sidney siegfried sigrid silvia simon simone simran
sinead siobhan sofia sofie sonia sophia sophie soren spencer stanislaw stanley stefan stefania
stefano stella stephane stephanie stephen steve steven stewart stuart sue sunil sunita
susan susanne suzanne sven svetlana sylvia sylvie szymon tadeusz tal tamara tamar tania
tanya tara tariq tarun tasha ted teresa terence terry tessa thabo theo theodore theresa
therese thierry thomas thorsten tim timothy tina tobias toby todd tom tomas tomasz
tommy toni tony torsten tracey tracy travis trevor tristan troy tunde tyler ulrich
ulrike urszula ursula usman ute valentina valerie vanessa vasilis vera verity veronica
vicente vicky victor victoria vijay vikram vincent vincenzo vinay violet virginia
vishal vivian volker wade walter wanda warren wayne wei wendy werner wesley whitney
wiktor wilhelm will willem william wilson winston wojciech wolfgang xavier xin yael
yan yang yann yasmin yves ying yonatan yosef yousef yuki yun yusuf yvonne
zachary zainab zara zbigniew zofia zoe
""".split())

# Romanised given names from Japan, Korea, China and Vietnam, kept apart for the
# same reason the short forms are: the main list was assembled out of European
# and South Asian names and hiroshi.tanaka@ was greeted "Hi there,".
#
# Four or more letters only. The two- and three-letter romanisations (ren, aoi,
# sora, min, hye) are where a name and some other language's word for a front
# desk collide, and this side of the test is the side that greets a filing
# cabinet by name when it is wrong.
_EAST_ASIAN_NAMES: frozenset[str] = frozenset("""
aiko akane akemi akiko akira ayaka ayako ayumi chiaki chieko chihiro daichi
daiki daisuke eiji emiko eunji eunju fumiko genki hanako haruka haruki haruto
hideki hideo hikaru hiroaki hiroko hiroshi ikuko itsuki jieun jihoon jimin
jisoo jiwoo junko junpei kaori kaoru kazuki kazuko kazuo keiko kenji kenta
kohei kosuke kumiko kyoko linh makoto mamoru manabu mariko masaaki masahiro
masako masaru masato mayumi michiko midori mika miki minho minji minjun minoru
misaki mitsuko nanami naoki naoko naoto noboru noriko nozomi reiko rina ryoko
ryosuke ryota sachiko saori satoko satoshi sayaka seiji seojun seoyeon shigeru
shinji shinya shiori shohei shota soojin sooyoung tadashi taeko taichi takahiro
takashi takeshi takumi tamiko tetsuya thanh tomoko tomomi toshiaki toshiko
tsuyoshi wenjie xiaoming yasuo yohei yoshiaki yoshiko yosuke youngmin yuichi
yuka yukari yukiko yumiko yusuke yuta yutaka yuuki zhihao ziyang
""".split())

# The short forms, kept apart because they are the ones the list keeps losing.
# "robert", "roberta" and "roberto" were all above and "rob" was not, so
# rob@ — the owner of a four-person firm, handing out the address he answers
# himself, the best lead in the file — got "Hi there,". A full name is easy to
# think of and its clipping is not, so they get their own list and their own
# rule: everything here is short, is a person and is never a job.
#
# Nothing here may be a function a business hands an address to. That is the
# whole cost of the accept side being positive — a wrong name greets a filing
# cabinet — and it is why "bill", "art", "may" and "dawn" are not here.
_SHORT_FORMS: frozenset[str] = frozenset("""
abe al bert bev bob chuck cliff clint curt cy deb don doug drew earl ed floyd
fran gene gerry gil gus hal hank jed jo joe jules kip larry li liz lou lyle
marty meg mel moe nat otto pat pete phil ray rex rob rod russ sal sid stan stu
vern vic viv walt wes zeke
""".split())

_GIVEN_NAMES = _GIVEN_NAMES | _EAST_ASIAN_NAMES | _SHORT_FORMS

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z'\-]{1,17}$")


def _first_name(lead: dict) -> str:
    """A first name to greet, or "there".

    A name someone typed into the lead record is taken at its word. A mailbox is
    not: it is only read as a person when it opens on a recognisable given name,
    on its own or in front of a surname ("mike.reid", "dev_patel"). "Hi there,"
    is what an unrecognised name costs; "Hi Surgery," is what the other way round
    costs, and that one is read by the practice manager it was addressed to.
    """
    for key in ("first_name", "contact_first_name", "contact_name", "owner"):
        raw = str(lead.get(key) or "").strip()
        if raw:
            first = raw.replace(",", " ").split()[0]
            if _NAME_RE.match(first):
                return first[:1].upper() + first[1:]
    local = str(lead.get("email") or "").split("@")[0].strip().lower()
    parts = [p for p in re.split(r"[._\-+]", local) if p]
    if parts and parts[0] in _GIVEN_NAMES and all(_NAME_RE.match(p) for p in parts):
        return parts[0][:1].upper() + parts[0][1:]
    return "there"


# A hostname shape, not a list of hostnames: dot-separated runs of word
# characters. Scraped text arrives as whatever was on the page, and `urlsplit`
# hands back a netloc for most of it — "javascript", "exa mple.com", "[" — none
# of which is a domain and all of which would render into the copy as one.
_HOST_RE = re.compile(r"^(?:[\w\-]+\.)+[\w\-]{2,}$", re.UNICODE)
# `urlsplit` deletes tabs and newlines rather than rejecting the URL, so
# "http://new\nline.com" comes back as a perfectly good "newline.com" that is
# not the host that was on the page. No URL contains whitespace; a candidate
# that does is not one.
_RAW_JUNK_RE = re.compile(r"[\s\x00-\x1f\x7f]")
_URL_MAX = 2048


def _domain(lead: dict, audit: dict) -> str:
    """The lead's host, or "".

    Total by construction. Every candidate is third-party text off a scraped
    page, and `urlsplit` raises on some of it — a bracketed netloc that is not a
    valid IPv6 literal is the one measured in the wild. One such record used to
    abort the whole campaign from four frames down.
    """
    for candidate in (lead.get("domain"), audit.get("final_url"), audit.get("url"),
                      lead.get("website")):
        raw = str(candidate or "").strip()
        if not raw or len(raw) > _URL_MAX or _RAW_JUNK_RE.search(raw):
            continue
        if "//" not in raw:
            raw = "//" + raw
        try:
            netloc = urllib.parse.urlsplit(raw).netloc
        except ValueError:
            continue
        host = netloc.lower().split("@")[-1].split(":")[0]
        if host.startswith("www."):
            host = host[4:]
        if len(host) <= 253 and _HOST_RE.match(host):
            return host
    return ""


# Markup as a scraper or a spreadsheet hands it over, both halves of it.
_TAG_RE = re.compile(r"<[^<>]*>")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _plain_name(raw) -> str:
    """A business name as text, whatever the CSV or the crawl put in the cell.

    "Acme &amp; Sons" is a live subject line's worth of markup: the entity
    renders literally in both parts of the message, and a tag renders as nothing
    at all in the HTML one and as itself in the plain-text one.

    Decoding once is not enough, because a decode can put back what the previous
    one took out: "&amp;lt;b&amp;gt;" decodes to "&lt;b&gt;" and then to "<b>".
    Decode and strip until the value stops changing, and only then run the
    sanitisers the rest of the module relies on — the em dash rule, and the
    control characters, one of which is the gap marker itself.
    """
    out = str(raw or "")
    for _ in range(3):
        step = _html.unescape(_TAG_RE.sub(" ", out))
        if step == out:
            break
        out = step
    out = _strip_dashes(_CONTROL_RE.sub(" ", _TAG_RE.sub(" ", out)))
    return re.sub(r"\s+", " ", out).strip()


def _gap_subject(gap: dict) -> str:
    """The neutral phrasing a gap goes into a subject line with.

    `core.audit` gives every gap a `subject_phrase` beside its `title` because
    the title is written for the middle of a sentence, where the copy around it
    carries the blow. "a site nobody has touched in years at Acme Plumbing Ltd"
    as the first line a stranger ever sees from you is an insult, not an opener.
    Older gap dicts (hand-built leads, CSV import) fall back to the title.
    """
    for key in ("subject_phrase", "title"):
        value = str(gap.get(key) or "").strip()
        if value:
            return value
    return ""


def _spoken_category(value: str) -> str:
    """Lower-case a Maps category for mid-sentence use, keeping acronyms."""
    words = [w if (w.isupper() and len(w) <= 4) else w.lower() for w in str(value or "").split()]
    return " ".join(w for w in words if w)


def _pick(items, key: str) -> str:
    """Stable choice from a list — the same lead always sees the same proof."""
    values = [str(i).strip() for i in (items or []) if str(i or "").strip()]
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    seed = int(hashlib.sha1((key or "").encode("utf-8", "replace")).hexdigest()[:8], 16)
    return values[seed % len(values)]


def _flatten(text: str) -> str:
    """Lower-case words with single spaces around every one of them."""
    return " %s " % re.sub(r"[^\w]+", " ", str(text or "").lower(), flags=re.UNICODE).strip()


def _recipient_anchors(name: str, domain: str, gaps: list) -> frozenset[str]:
    """Phrases only this recipient's own record could have supplied.

    A denylist of phrasings has to describe the infinite set of ways to write a
    lie, and loses. This describes the finite set of things the sender actually
    knows about the reader: the name on the record, the host that was crawled,
    and the gap the crawl found on it. Nothing else is evidence.

    The name counts whole, never word by word. "Acme Plumbing" would otherwise
    make "plumbing" an anchor, and "I set this up for a plumbing firm last
    month" would read as an observation about Acme.
    """
    anchors = set()
    flat_name = _flatten(name).strip()
    if len(flat_name.replace(" ", "")) >= 5:
        anchors.add(flat_name)
    sources = [domain] + [str(g.get(k) or "") for g in gaps[:2]
                          for k in ("title", "subject_phrase")]
    for source in sources:
        anchors.update(w for w in _flatten(source).split() if len(w) >= 5)
    return frozenset(anchors)


# The accept-list above answers one question — does this sentence refer to the
# reader? — and a sentence can answer it while still claiming something the
# sender has not earned: "Harbourvale Joinery is the eleventh joinery firm in
# Leeds we have set this up for" names the reader in its first three words. Both
# halves are required. The sentence must refer to the reader *and* assert
# nothing about the sender's history, client count, ratings or results.
#
# This half is a denylist, and a denylist that had to describe every way of
# writing a lie is what rounds 1-4 lost with. It only ever sees a sentence that
# has already anchored, so the space it describes is not "every lie" but "every
# lie told while naming the reader's own business", and it fails towards
# silence: a sentence it is wrong about is a sentence deleted, which costs the
# email its personalisation and nothing else.

# Every inflection below is written out. A trailing `\w*` reaches to the end of
# whatever word it started in — `mein\w*` matches "Meinhardt Joinery", `compan\w+`
# matches "companion" — and this list is read against business names.
#
# The sender in their own sentence. There is exactly one thing they may say
# about themselves — what they looked at — because that is a statement about the
# reader's site wearing a first-person subject. Everything else is a claim, and
# a first-person form this list does not recognise is read as one, which is why
# the French and German pronouns need no observer forms of their own.
_OBSERVER_RE = re.compile(
    r"\b(?:i|we)\s+(?:have\s+|had\s+|just\s+|also\s+|only\s+|already\s+){0,2}"
    r"(?:noticed|noted|notice|saw|see|seen|read|spotted|spot|looked|looking|"
    r"checked|opened|clicked|tried)\b")
_FIRST_PERSON_RE = re.compile(
    r"\b(?:i|we|us|our|ours|my|"
    r"j|je|nous|notre|nos|mon|mes|"
    r"ich|wir|uns|unser|unsere|unseren|unserem|unserer|unseres|"
    r"mein|meine|meinen|meinem|meiner|meines)\b")

# Somebody who is neither the reader nor the sender: the other clients, the
# firms in the case study, the ones on the waiting list. A trade name cannot be
# enumerated — every trade and every language invents its own — but the
# comparison in front of it can be, and no sentence needs one to describe the
# reader's own site.
_OTHERS_RE = re.compile(
    r"\b(?:other|others|another|else|elsewhere|similar|similarly|likewise|"
    r"nearby|neighbouring|neighboring|competitors?|rivals?|peers|"
    r"autre|autres|ailleurs|similaire|similaires|voisin|voisine|voisins|"
    r"voisines|concurrent|concurrents|"
    r"andere|anderen|anderem|anderer|anderes|weitere|weiteren|weiterer|"
    r"weiteres|sonstige|sonstigen|ähnliche|ähnlichen|ahnliche|ahnlichen|"
    r"benachbarte|benachbarten)\b")
# Businesses in the plural. One business is what the whole email is about; two
# or more of them, in a sentence naming the reader, are the ones the sender is
# comparing them to. "clients" and "customers" are deliberately not here — the
# reader's own customers are a fair thing to write about, and "our clients" is
# already a first-person claim.
_OTHER_BUSINESSES_RE = re.compile(
    r"\b(?:businesses|firms|companies|shops|workshops|outfits|trades|tradespeople|"
    r"entreprises|sociétés|societes|ateliers|artisans|"
    r"unternehmen|betriebe|betrieben|firmen|werkstätten|werkstatten)\b")
# A count of businesses. The count alone is innocent — "asks for nine fields"
# describes the reader's own form — so it only reads as a claim in front of a
# word for a business, singular ones included.
_COUNTS = (r"\d+|"
           r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
           r"second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
           r"eleventh|twelfth|dozens|hundreds|thousands|several|many|most|"
           r"countless|numerous|"
           r"onze|douze|deuxième|deuxieme|troisième|troisieme|douzième|douzieme|"
           r"dizaines|centaines|milliers|plusieurs|nombreux|nombreuses|"
           r"elf|zwölf|zwolf|zwölfte|zwolfte|zweite|dritte|elfte|elften|"
           r"dutzend|dutzende|dutzenden|hunderte|tausende|viele|mehrere|zahlreiche")
# The singular forms too, because "the twelfth workshop in Leeds" is one
# business standing for a hundred. "clients" and "customers" are here and not in
# the plural list below: the reader's own customers are a fair thing to write
# about, and "our clients" is already caught as a first-person claim.
_PARTIES = (r"business|businesses|firm|firms|company|companies|shop|shops|"
            r"workshop|workshops|trade|trades|tradespeople|outfit|outfits|"
            r"client|clients|customer|customers|"
            r"entreprise|entreprises|société|sociétés|societe|societes|"
            r"atelier|ateliers|artisan|artisans|"
            r"unternehmen|betrieb|betriebe|betrieben|firma|firmen|"
            r"werkstatt|werkstätten|werkstatten|kunde|kunden")
_COUNTED_PARTY_RE = re.compile(
    r"\b(?:%s)\b(?:\s+\w+){0,3}\s+(?:%s)\b" % (_COUNTS, _PARTIES))
# "firms like this one", "Betriebe wie Ihres" — the comparison itself, with the
# count left out.
_LIKE_THIS_RE = re.compile(
    r"\b(?:like|comme|wie)\s+(?:this|these|those|yours|"
    r"le\s+vôtre|la\s+vôtre|le\s+votre|la\s+votre|celui|celle|"
    r"ihres|ihre|ihrem|ihren)\b")
# A credential: somebody else's verdict on the sender. None of this is knowable
# from a crawl of the reader's website, in any of the three languages.
_CREDENTIAL_RE = re.compile(
    r"\b(?:rated|rating|ratings|stars?|reviews?|reviewed|awards?|awarded|"
    r"trusted|proven|recognised|recognized|testimonials?|endorsed|"
    r"case\s+stud(?:y|ies)|track\s+record|waiting\s+list|"
    r"avis|étoiles|etoiles|récompense|récompenses|récompensé|recompense|"
    r"recompenses|réputation|réputé|témoignage|témoignages|temoignage|"
    r"temoignages|référence|références|recommandé|recommandée|recommandés|"
    r"bewertung|bewertungen|sterne|auszeichnung|auszeichnungen|empfohlen|"
    r"empfehlung|empfehlungen|referenz|referenzen|bewährt|bewahrt|erprobt)\b")


def _sender_claim(text: str, anchors: frozenset[str], sender: frozenset[str]) -> bool:
    """Does this sentence assert something about the sender?

    Four tells, and any one of them is enough: the sender as an actor rather
    than as somebody who read a page, a third party the reader is being compared
    to, a count of businesses, or a credential somebody else awarded.

    What the recipient's own record supplied is struck out first, longest match
    down. Otherwise a business called Sterne Fabrication or Referenz Bau reads
    as a credential in every sentence anybody writes about it — and unlike a
    misjudged sentence, which costs one field once, a name that trips a rule
    costs that lead its personalisation for ever.
    """
    flat = _flatten(text)
    for anchor in sorted(anchors, key=len, reverse=True):
        flat = flat.replace(" %s " % anchor, " ")
    if any(" %s " % word in flat for word in sender):
        return True
    if _FIRST_PERSON_RE.search(_OBSERVER_RE.sub(" ", flat)):
        return True
    return bool(_OTHERS_RE.search(flat) or _OTHER_BUSINESSES_RE.search(flat)
                or _COUNTED_PARTY_RE.search(flat) or _LIKE_THIS_RE.search(flat)
                or _CREDENTIAL_RE.search(flat))


def _sender_words(profile: dict, anchors: frozenset[str]) -> frozenset[str]:
    """The sender's own names, as phrases a sentence about the reader will not hold.

    Skipped when the name is part of what is known about the recipient, so a
    seller mailing their own trade cannot silence every sentence they write.
    """
    words = set()
    for key in ("company", "sender_name"):
        flat = _flatten(profile.get(key)).strip()
        if len(flat.replace(" ", "")) >= 4 and not any(flat in a for a in anchors):
            words.add(flat)
    return frozenset(words)


def _observed(text: str, anchors: frozenset[str],
              sender: frozenset[str] = frozenset()) -> str:
    """Keep the sentences that refer to the recipient and only to them.

    The model writes three fields and all three have the same job: say something
    about *this* business. A sentence that names none of it is not an
    observation, whatever it is — an invented track record, a rating nobody
    gave, a boast about the sender's own company. The test is what a sentence
    contains, not what it avoids, so there is nothing to phrase around: writing
    the claim in the third person, in lower case or under a different name does
    not get it past.

    Anchoring is necessary and not sufficient. A sentence that names the reader
    and then sells past them — "your booking page is the same one we fixed for
    four other Leeds joiners" — is two claims, and the second one is still
    fabricated. `_sender_claim` takes those out.

    An empty result costs the email its personalisation and nothing else. Every
    field this guards is optional and every template stands without it.
    """
    if not text or not anchors:
        return ""
    pieces = [p for p in _SENTENCE_SPLIT_RE.split(text) if p.strip()]
    kept = [p for p in pieces
            if any(" %s " % anchor in _flatten(p) for anchor in anchors)
            and not _sender_claim(p, anchors, sender)]
    out = " ".join(kept).strip()
    # Only when the opening sentence was one of the deleted ones: a lower-case
    # start is otherwise how the model wrote it, and subjects are asked for in
    # lower case.
    if out and kept[0] != pieces[0]:
        out = out[:1].upper() + out[1:]
    return out


def _as_sentence(text: str) -> str:
    """A stored line rendered as a paragraph of its own: capital, then a stop.

    `proof_point` sits alone between two blank lines and comes off the profile
    exactly as it was typed. The settings placeholder teaches a mid-sentence
    fragment, so the shipped preview puts a lowercase unterminated paragraph
    between two correct ones and the whole email reads as generated.

    Exclamation marks are stripped here for the same reason they are stripped
    from subjects and from model output: this paragraph stands alone, so a
    shouted one is the loudest line in the message. A line with no letter or
    digit in it is not a proof point at all and renders as nothing.
    """
    out = re.sub(r"\.?\s*!+", ".", str(text or "")).strip()
    if not re.search(r"[^\W_]", out, flags=re.UNICODE):
        return ""
    out = out[:1].upper() + out[1:]
    return out if out.rstrip("”’\"')]").endswith((".", "?")) else out + "."


def _clean_snippet(text: str, limit: int = 260) -> str:
    """Fold untrusted text into the copy: no dashes, no links, no placeholders.

    Applies to model output and to audit evidence alike. Both are written by a
    machine and both end up inside a sentence a human is meant to have written.

    The model is asked for no exclamation marks and no greeting; asking is not
    enforcement. Its opener is pasted straight under "Hi Rob,", so a greeting it
    wrote itself would be the second one the reader sees, and a stranger who
    shouts in his first line is a stranger who bought a list.

    Greetings are stripped in a loop, not once. A model that opens "Hi Jane
    Smith, hi again," has written two, and taking one leaves the other standing
    under the real greeting — the exact doubling this function exists to stop.

    What a link or a placeholder leaves behind is a hole, not a shorter
    sentence: "I saw {{business_name}} still uses a [contact form] on
    {{website_domain}}" excised in place ships as "I saw still uses a on". The
    body path deletes a sentence that lost a value from its middle, and this
    path deletes it the same way, off the same marker.
    """
    out = _strip_dashes(str(text or "")).replace("\r", " ").replace("\n", " ")
    out = _URL_RE.sub(_GAP, out)
    out = re.sub(r"\{\{[^{}]*\}\}|\[[^\[\]]{0,40}\]", _GAP, out)
    out = re.sub(r"\.?\s*!+", ".", out)
    out = re.sub(r"\.{2,}", ".", out)
    greeted = False
    while True:
        match = _GREETING_RE.match(out)
        if not match or not match.end():
            break
        greeted = True
        out = out[match.end():]
    holed = _GAP in out
    led = out.lstrip().startswith(_GAP)
    out = _tidy_line(_drop_holed_sentences(out), drop_holed=False)
    out = re.sub(r"\s+([,.;:])", r"\1", re.sub(r"\s{2,}", " ", out)).strip()
    if greeted or led:
        out = out[:1].upper() + out[1:]
    # An unterminated fragment cannot be dropped as a sentence, so it is trimmed
    # instead: "form posts to https://..." must not ship as "form posts to".
    if holed and not out.endswith((".", "?")):
        out = _trim_tail(out)
    return _clip_snippet(out, limit)


def _clip_snippet(text: str, limit: int) -> str:
    """Cut `text` to `limit` on a word boundary, and close the sentence."""
    if len(text) <= limit:
        return text
    return _trim_tail(text[:limit].rsplit(" ", 1)[0]) + "."


# What a model field is cleaned to before its sentences are judged; the field's
# own limit is applied to whatever survives. A sentence has to be judged whole,
# and clipping first is what hid a claim: the subject limit cut "with us" off
# the end of "Harbourvale Joinery joins a long list of joinery firms that fixed
# their online booking with us", and the half left standing read as an
# observation about Harbourvale.
_SNIPPET_MAX = 2000


def _observed_field(ai: dict, key: str, limit: int, anchors: frozenset[str],
                    sender: frozenset[str]) -> str:
    """One model field, cleaned, judged sentence by sentence, then clipped."""
    return _clip_snippet(
        _observed(_clean_snippet(ai.get(key), _SNIPPET_MAX), anchors, sender), limit)


# The opt-out sentence, with a slot for the address, and the same sentence for
# when there is no address to offer. They are constants rather than literals in
# `build_context` because two things have to agree about the wording: what is
# written when a campaign is planned, and `retarget_unsubscribe`, which rewrites
# the address inside it when the message finally leaves.
UNSUBSCRIBE_LINE = (
    'Not the right person? Reply "unsubscribe" or write to %s and I will stop.')
UNSUBSCRIBE_LINE_NO_ADDRESS = (
    'Not the right person? Reply "unsubscribe" and I will stop.')

_LINE_BEFORE, _LINE_AFTER = UNSUBSCRIBE_LINE.split("%s")
# Anchored on the half of the sentence `html.escape` leaves alone, so one
# pattern finds the address in the plain-text part and in the HTML part — where
# the quotes around "unsubscribe" are `&quot;` and the word itself has become an
# anchor, and only the tail of the sentence still reads the same in both.
_FOOTER_ADDRESS_RE = re.compile(
    "(%s)(\\S+?)(%s)" % (re.escape(_LINE_BEFORE.rsplit('"', 1)[-1]), re.escape(_LINE_AFTER)))
_FOOTER_HREF_RE = re.compile(r"mailto:[^\"'>\s]*\?subject=unsubscribe")


def unsubscribe_address(profile: dict, settings: dict, account_email: str = "") -> str:
    """The one mailbox a message's opt-out routes point at.

    `account_email` is the account the message is actually sent from. It comes
    second only to the address the user typed in Settings, and it comes ahead of
    `reply_to`, because `core.mailer.build_message` resolves `List-Unsubscribe`
    the same way — setting first, sending account second — and a footer that
    resolved differently offered the reader a second, different route out. With
    more than one account enabled the two picked different mailboxes, and the
    footer's was whichever account happened to be first in the list.

    `reply_to` survives as the last resort rather than the second, because it is
    the only route left when no sending account is configured at all, and
    because it is the one address here the app has no credentials for and
    therefore cannot promise to read.
    """
    raw = str((settings or {}).get("unsubscribe_mailto") or "").strip()
    if not raw:
        raw = str(account_email or "").strip()
    if not raw:
        for account in (settings or {}).get("smtp_accounts") or []:
            if isinstance(account, dict) and account.get("enabled", True):
                raw = str(account.get("email") or "").strip()
                if raw:
                    break
    if not raw:
        raw = str((profile or {}).get("reply_to") or "").strip()
    if raw.lower().startswith("mailto:"):
        raw = raw[7:]
    return raw.split("?")[0].strip()


def retarget_unsubscribe(body: str, address: str) -> str:
    """Point the footer's opt-out route at `address`, sentence and mailto alike.

    A body is written when the campaign is planned and the account it leaves
    from is chosen when it comes due — a capped or benched account hands the
    message to the next one — so the address baked into the footer is a guess
    until the message is built. This is where the guess is corrected, against
    the same address `List-Unsubscribe` is about to carry, so the reader is
    never offered two different ways out.

    A body with no footer in it, or no address to write, comes back unchanged.
    """
    text = str(body or "")
    address = re.sub(r"[\s<>\"']+", "", str(address or ""))
    if not text or not address:
        return text
    text = _FOOTER_ADDRESS_RE.sub(lambda m: m.group(1) + address + m.group(3), text)
    return _FOOTER_HREF_RE.sub(
        "mailto:%s?subject=unsubscribe" % urllib.parse.quote(address, safe="@"), text)


def build_context(lead: dict, audit: dict, ai: dict, profile: dict,
                  settings: dict, account_email: str = "") -> dict:
    """Merge fields for one lead.

    Every key in `MERGE_FIELDS` is present. Fields that read as part of a
    sentence carry a neutral fallback ("your business", "your area") rather than
    an empty string, because a shorter sentence beats a hole in the middle of
    one. `calendar_link` falls back to the company website so the copy rule of
    exactly one link still holds when no calendar is configured, and `ai_ps`
    arrives already prefixed "P.S." so the line disappears cleanly without it.

    `account_email` is the account this message will be sent from, when the
    caller already knows it, and it decides the footer's opt-out address — see
    `unsubscribe_address`. A caller that does not know yet writes the footer for
    the first account that could send, and `retarget_unsubscribe` corrects it
    when the message is built.
    """
    lead = dict(lead or {})
    audit = dict(audit or {})
    ai = dict(ai or {})
    profile = dict(profile or {})
    settings = dict(settings or {})

    gaps = [g for g in (audit.get("gaps") or []) if isinstance(g, dict)]
    # Which gap the email leads with is not "the worst one", it is "the worst one
    # there is something to say about". Every template that names `gap_1` follows
    # it with an offer, so a headline the catalogue cannot answer either borrows
    # a service from an unrelated line — "a slow site, so we build approval
    # systems" — or leaves the offer answering a different question than the one
    # the reader was just asked to think about. `core.audit` already sorts these
    # last; doing it again here covers a hand-built lead and a CSV import, which
    # arrive in whatever order they were written.
    answerable = [g for g in gaps if services_for_gaps([g])]
    taken = {id(g) for g in answerable}
    # A gap with no offer is still worth knowing, so it stays reachable as
    # `gap_2`, where it is detail behind a headline and not a promise of its own.
    headline = answerable + [g for g in gaps if id(g) not in taken] if answerable else []

    chosen = [s for s in (profile.get("services") or []) if str(s or "").strip()]
    # A profile still holding the whole seeded catalogue is not a choice, so the
    # curated pitch leads; a narrowed list is a choice and leads instead.
    narrowed = bool(chosen) and len(chosen) < len(DEFAULT_SERVICES)
    top_up = chosen + DEFAULT_PITCH_SERVICES if narrowed else DEFAULT_PITCH_SERVICES + chosen
    services = services_for_gaps(gaps, extra=top_up) + ["", "", ""]

    domain = _domain(lead, audit)
    calendar = str(profile.get("calendar_link") or "").strip()
    website = str(profile.get("website") or "").strip()
    # A name is only a name when there is something in it to read. "!!!" is
    # present, so the fallback never engaged, and it rendered raw into the body.
    name = _plain_name(lead.get("name"))
    if not re.search(r"[^\W_]", name, flags=re.UNICODE):
        name = ""
    anchors = _recipient_anchors(name, domain, gaps)
    sender = _sender_words(profile, anchors)
    ps = _observed_field(ai, "ps", 180, anchors, sender)

    ctx = {
        "business_name": name or _NEUTRAL_NAME,
        "first_name": _first_name(lead),
        # Adjective, not a noun: the fallback has to work in every slot the
        # templates put it in, and "the local that answers first" is not English.
        # Keep `category` in front of a noun ("{{category}} businesses").
        "category": _spoken_category(lead.get("category")) or "local",
        "website_domain": domain or "your site",

        "gap_1": str(headline[0].get("title") or "").strip() if headline else "",
        "gap_2": str(headline[1].get("title") or "").strip() if len(headline) > 1 else "",
        "gap_1_evidence": _clean_snippet(headline[0].get("evidence"), 90) if headline else "",
        "gap_1_subject": _gap_subject(headline[0]) if headline else "",
        "service_1": services[0],
        "service_2": services[1],
        "service_3": services[2],

        "ai_subject": _observed_field(ai, "subject", SUBJECT_MAX + 20, anchors, sender),
        "ai_opener": _observed_field(ai, "opener", 260, anchors, sender),
        "ai_ps": ("P.S. " + ps) if ps else "",

        "sender_name": str(profile.get("sender_name") or "").strip(),
        "sender_title": str(profile.get("sender_title") or "").strip(),
        "company": str(profile.get("company") or "").strip(),
        "company_website": website,
        "calendar_link": calendar or website,
        "phone": str(profile.get("phone") or "").strip(),
        "postal_address": str(profile.get("postal_address") or "").strip(),
        "proof_point": _as_sentence(
            _pick(profile.get("proof_points"), domain or str(lead.get("email") or ""))),
    }

    address = unsubscribe_address(profile, settings, account_email)
    ctx["unsubscribe_email"] = address
    ctx["unsubscribe_line"] = (
        UNSUBSCRIBE_LINE % address) if address else UNSUBSCRIBE_LINE_NO_ADDRESS

    out = {k: _strip_dashes(str(v)) for k, v in ctx.items()}
    # Not merge fields, and deliberately not strings: nothing renders these. They
    # are how the campaign and the GUI can count what went out as a form letter.
    out["personalised"] = any(out[k] for k in _PERSONAL_FIELDS)
    out["generic_reason"] = "" if out["personalised"] else _generic_reason(lead, audit)
    return out


# ── Personalisation ──

# What makes an email this recipient's rather than anybody's. The business name
# is not on the list: it is a merge field, and three generic paragraphs with the
# reader's own name at the top of them is the form letter this is here to count.
_PERSONAL_FIELDS: tuple[str, ...] = ("gap_1", "ai_opener", "ai_ps", "ai_subject")

# Why an email came out generic, singular and plural. Both forms live here
# rather than in the GUI because the Leads table names one lead, the plan
# summary names a group, and the two have to agree about the same crawl.
GENERIC_REASONS: dict[str, tuple[str, str]] = {
    "no_website": ("there is no website on the record",
                   "those leads have no website on the record"),
    "not_audited": ("the site has not been crawled yet",
                    "those sites have not been crawled yet"),
    "unreachable": ("the site could not be reached",
                    "those sites were unreachable"),
    "nothing_found": ("nothing specific was found on the site",
                      "nothing specific was found on those sites"),
}


def _generic_reason(lead: dict, audit: dict) -> str:
    """Which `GENERIC_REASONS` code explains an email with nothing in it."""
    if not _domain(lead, audit):
        return "no_website"
    if not audit:
        return "not_audited"
    if not audit.get("reachable"):
        return "unreachable"
    return "nothing_found"


def generic_reason(code: str, plural: bool = False) -> str:
    """A `generic_reason` code as a sentence fragment. Unknown codes read blank."""
    return GENERIC_REASONS.get(str(code or ""), ("", ""))[1 if plural else 0]


def personalisation(lead: dict, audit: dict, ai: dict, profile: dict | None = None,
                    settings: dict | None = None) -> tuple[bool, str]:
    """(is this lead's email personalised, why not) for one lead.

    Read off the context the send loop would actually render, so the Leads
    table, the plan summary and the Stats tab cannot drift from what was sent.
    The accept-list in `_observed` is deliberately willing to discard every
    model sentence it cannot tie to this recipient — the right trade, because
    the alternative is claiming something the sender does not know — and this is
    how many times it happened.
    """
    gaps = [g for g in ((audit or {}).get("gaps") or []) if isinstance(g, dict)]
    if gaps and str(gaps[0].get("title") or "").strip():
        # The headline gap renders into the body by itself, so a lead that has
        # one is personalised whatever the model wrote. Answered without
        # building the context because it is the common case and the Leads
        # table asks this question once per row.
        return True, ""
    ctx = build_context(lead, audit, ai, profile or {}, settings or {})
    return bool(ctx["personalised"]), str(ctx["generic_reason"])
