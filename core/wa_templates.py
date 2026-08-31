"""Outreach copy for WhatsApp, in WhatsApp's register.

This is not `core.templates` with the HTML taken out. The email side writes to
an inbox a business owner opens at a desk; this writes to a chat thread on a
phone, from a number the reader has never seen, and everything about the shape
of the message follows from that one difference:

* **Under sixty words.** A hundred-and-twenty-word email is normal. The same
  text in a chat bubble is a wall, and a wall from an unknown number is
  reported rather than read. `WA_MAX_WORDS` is measured on the message as it
  will actually arrive, opt-out line included.
* **No subject, no signature block, no footer, no HTML.** `render_wa` returns
  one string. A `Template` still carries a `subject` field, because the shape
  is shared with the email store so the settings editor can edit both with the
  component it already has -- but nothing sends it, and `validate_wa_template`
  says so to anybody who fills it in.
* **One question, at most one link, and no calendar link on a first message.**
  A stranger who opens with a booking link is selling; a stranger who opens
  with a question is talking.
* **It opens by saying who is writing and why *this* business.** The reason is
  the audit gap -- the same `gap_1` the email leads with, from the same
  `build_context`. That specificity is the entire value of the message. Without
  it this is indistinguishable from the spam everyone gets, and on WhatsApp
  being indistinguishable from spam is what ends the number.

**Every message carries a plain opt-out line, and it is not optional.**
`render_wa` appends one to any message that does not already teach a way to
stop it, whatever the user edited, and it names a word `wa_opt_out_words` will
actually honour rather than one the copy hard-coded. It is there for two
reasons and the second is the load-bearing one: it is safer legally, and it
materially reduces being *reported*, which is the thing that actually gets a
number banned. A ban is usually permanent, so the eight words are cheap.

The offer comes from the user's own catalogue and comes through the shared
context. `service_1..3` are resolved by `core.templates.services_for_gaps` from
the same `AUTO_ARMY_SERVICES` list the email side pitches, so a service the
user renames or removes changes both channels at once and the gap-to-service
mapping cannot drift between them.

Storage mirrors `core.templates` exactly -- a JSON file of overrides laid over
a shipped list, an edit that takes effect for every caller, a reset that puts
the original back -- and deliberately in a *second* file, so a WhatsApp
template can never be picked for an email campaign or the reverse.
"""

from __future__ import annotations

import hashlib
import json
import os
import re

from core import templates as _t
from core.templates import Template

# The email module's private machinery, borrowed rather than re-typed. Every
# name below is a pure function of its arguments, and every one of them is the
# reason a merge token cannot reach a real conversation: `_resolve` turns an
# unresolved field into a gap marker, `_tidy` deletes the marker along with the
# punctuation and the sentence that belonged to it. Re-implementing that here
# would mean two renderers that have to stay bug-for-bug identical, and the
# first one to drift is the one that ships "Hi {{first_name}}" to a phone.
_resolve = _t._resolve
_tidy = _t._tidy
_clip_snippet = _t._clip_snippet
_coerce = _t._coerce
_as_dict = _t._as_dict
_read_store = _t._read_store
_store_stamp = _t._store_stamp
_finding = _t._finding
_raw_field = _t._raw_field
_token_findings = _t._token_findings
_brace_findings = _t._brace_findings
_shouting_findings = _t._shouting_findings
_spam_findings = _t._spam_findings
_SENTENCE_SPLIT_RE = _t._SENTENCE_SPLIT_RE
_TOKEN_RE = _t._TOKEN_RE
_FIELD_RE = _t._FIELD_RE
_URL_RE = _t._URL_RE
_SLUG_RE = _t._SLUG_RE

# Everything `render_wa` will resolve, which is the whole email set: a user who
# reaches for a field should get its value rather than the machinery. What the
# editor should *offer* on this channel is the narrower `WA_MERGE_FIELDS` below.
MERGE_FIELDS = _t.MERGE_FIELDS


# ── Copy rules ──

# Measured on the rendered message, opt-out line included, because that is what
# lands in the bubble. Sixty is not a style preference: a WhatsApp message longer
# than about four lines is collapsed behind "Read more" on most phones, and a
# cold message whose first visible words are a pitch is a reported message.
WA_MAX_WORDS = 60

# A first touch asks one thing. Two questions from a stranger is an interview.
WA_MAX_QUESTIONS = 1

# At most one link, and never a calendar link on a first touch: booking a slot
# is what somebody does after they have decided to talk to you, and asking for
# it in the opening line is the single clearest tell of an automated blast.
WA_MAX_LINKS = 1

# What the model's opener is cut to before it goes into a bubble. `build_context`
# already clips it to 260 characters for an email and already ran it through the
# `_observed` guard, so an invented track record never gets this far; this is the
# phone's budget on top of that, applied to whole sentences so a clipped
# observation is never half a claim.
WA_AI_OPENER_MAX = 160


# ── The opt-out ──

# The reply this line teaches. It has to be a word `core.whatsapp.matches_opt_out`
# will honour against `wa_opt_out_words`, or the app invites a reply it then
# ignores -- which is worse than not offering one, because the reader who typed
# it and got another message is the reader who reports the number.
WA_OPT_OUT_WORDS: tuple[str, ...] = ("stop", "unsubscribe", "remove me", "do not message")

WA_OPT_OUT_LINE = "Reply STOP and I will not message again."

# An *instruction* to send one of those words, not merely the word itself. A
# message that happens to contain "stop" ("the van stops outside") is not an
# opt-out line, and treating it as one is how a first touch goes out with no way
# to end it.
_OPT_OUT_VERBS = r"reply|respond|answer|text|send|type|write|message|msg|hit"


def _opt_out_re(words) -> re.Pattern:
    alternatives = "|".join(
        r"\s+".join(re.escape(part) for part in str(word or "").split())
        for word in (words or ()) if str(word or "").strip()
    ) or re.escape("stop")
    return re.compile(
        r"(?:%s)\s+(?:back\s+)?[\"'“‘]?(?:%s)\b" % (_OPT_OUT_VERBS, alternatives),
        re.I)


_DEFAULT_OPT_OUT_RE = _opt_out_re(WA_OPT_OUT_WORDS)

# The same instruction with any shouted word behind it, watched or not. This is
# what finds "Reply REMOVE and I will take you off" -- a line that looks like an
# opt-out to the reader, is not one to the reply watcher, and therefore ends with
# somebody who typed the word getting the follow-up anyway. That reader reports
# the number, which is the outcome the opt-out line exists to prevent.
# The verb is matched case-insensitively and the word behind it is not: the
# shouted word is the whole signal, and a lower-case "reply stop" is already
# answered by `has_opt_out`.
_TEACHES_OPT_OUT_RE = re.compile(
    r"(?i:%s)\s+(?:back\s+)?[\"'“‘]?([A-Z][A-Z ]{1,18}[A-Z])\b" % _OPT_OUT_VERBS)


def has_opt_out(text: str, words=None) -> bool:
    """Does this message tell the reader how to stop it?

    True only for an instruction naming a word the reply watcher recognises, so
    a line teaching a word the app does not watch for reads as no opt-out at all
    -- which is the honest answer: the reader would type it and be messaged
    again anyway.
    """
    body = str(text or "")
    if not body.strip():
        return False
    pattern = _DEFAULT_OPT_OUT_RE if words is None else _opt_out_re(words)
    return bool(pattern.search(body))


def opt_out_line(words=None) -> str:
    """The opt-out sentence, naming a word the reply watcher will honour.

    `WA_OPT_OUT_LINE` whenever "stop" is one of the watched words, which is the
    shipped default and the word a phone keyboard makes easiest to type. A user
    who has edited `wa_opt_out_words` and dropped it gets a line naming the
    first word they kept -- because the alternative is a message inviting a
    reply the app then ignores, and the reader who typed it is exactly the
    reader who reports the number.
    """
    watched = [str(w or "").strip() for w in (words or ()) if str(w or "").strip()]
    if not watched or any(w.lower() == "stop" for w in watched):
        return WA_OPT_OUT_LINE
    return "Reply %s and I will not message again." % watched[0].upper()


def _mask_opt_out_words(text: str) -> str:
    """Lower-case the opt-out word before a shouting check reads it.

    STOP is four capitals, which is exactly what `_shouting_findings` flags. The
    one word this channel requires in capitals is the one word a caps check must
    not complain about, and a validator that warns on every correct template is
    a validator the user stops reading.
    """
    out = str(text or "")
    for word in WA_OPT_OUT_WORDS:
        upper = word.upper()
        if upper != word:
            out = out.replace(upper, word)
    return out


# ── Templates ──

# Read every one of these as the person receiving it: 11am on a Tuesday, a
# notification from a number with no name against it, thumb already hovering
# over Block. What buys the next four seconds is that the second line could only
# have been written to them.
#
# The mechanical rules the copy below obeys, all of them inherited from the email
# side because the renderer is the same one:
#
# * A sentence holding a field that can render empty is written *last* on its
#   line. `_tidy` deletes a sentence that lost a value from its middle, and it
#   takes no notice of what was written after it, so anything standing behind
#   such a sentence is left pointing at nothing. `gap_1`, `sender_name` and
#   `company` all vanish on a real lead; `first_name`, `business_name`,
#   `website_domain` and `service_1` carry fallbacks and never do.
# * Nothing follows `{{gap_1}}` but punctuation. A gap title is a noun phrase of
#   unknown number -- "no online booking" and "quotes handled by hand" both land
#   there -- so nothing may agree with it.
# * No verb agrees with `{{service_1}}` either, for the same reason: it is
#   "appointment booking" for one gap code and "HR processes" for another.
# * No figure the sender cannot know. "one" is the only number here.
# * No `{{category}}`. On the email side it is fenced off from every first-person
#   sentence because "we build this for nail salons" is a track record assembled
#   out of the reader's own Google Maps listing. In sixty words there is no room
#   to use it safely, so it is not used at all.
# * No claim about a feature of their site. What the crawl established arrives as
#   `gap_1` and nowhere else; everything else the copy wants to know, it asks.
# * No signature. The name is in the second line, where a person puts it.
WA_BUILTIN_TEMPLATES: list[Template] = [
    # The plainest version of the whole pitch: here is the thing I noticed, here
    # is what we would build, is it already handled. Everything else in this
    # list is a different way of not leading with the offer.
    Template(
        id="wa_gap",
        name="Headline gap",
        step=0,
        subject="",
        body=(
            "Hi {{first_name}},\n"
            "This is {{sender_name}} from {{company}}.\n"
            "\n"
            "I read {{website_domain}} before messaging, and one thing stood out: "
            "{{gap_1}}.\n"
            "\n"
            "We build {{service_1}}, so nothing waits on someone remembering.\n"
            "\n"
            "Worth a look?"
        ),
    ),
    # No offer at all. The question is the whole message and the gap is only the
    # reason for asking it, which is the version most likely to get a reply from
    # somebody who has never heard of the sender.
    Template(
        id="wa_question",
        name="One question",
        step=0,
        subject="",
        body=(
            "Hi {{first_name}},\n"
            "I am {{sender_name}} at {{company}}.\n"
            "\n"
            "One question: when a new enquiry comes in at {{business_name}}, who picks "
            "it up?\n"
            "\n"
            "I ask because I looked at {{website_domain}} first, and noticed "
            "{{gap_1}}."
        ),
    ),
    # Sells the symptom rather than the finding: the reader recognises the job
    # being redone by hand long before they care what a crawler saw.
    Template(
        id="wa_manual",
        name="Done by hand",
        step=0,
        subject="",
        body=(
            "Hi {{first_name}},\n"
            "My name is {{sender_name}} from {{company}}.\n"
            "\n"
            "The week goes into the job somebody redoes by hand. On "
            "{{website_domain}} that looked like {{gap_1}}.\n"
            "\n"
            "We build {{service_1}} instead. Shall I show you?"
        ),
    ),
    # Asks before sending anything. The lowest-pressure opening there is, and the
    # only one whose question can be answered with a single word by somebody who
    # is standing on a job site.
    Template(
        id="wa_permission",
        name="Ask first",
        step=0,
        subject="",
        body=(
            "Hi {{first_name}},\n"
            "This is {{sender_name}} from {{company}}.\n"
            "\n"
            "Can I send you one idea for {{business_name}}? Ignore it if it is not "
            "useful.\n"
            "\n"
            "The idea comes from reading {{website_domain}}: {{gap_1}}."
        ),
    ),
    # One chaser, and there is no second one. `wa_followup_max_steps` is 1
    # because a third message to a number that has not replied is what a
    # recipient reports, and a report is what bans the number.
    #
    # It does not write the opt-out itself. `render_wa` appends one naming a
    # word the user's own reply watcher honours, and a template that spelt STOP
    # into its body would keep saying STOP after somebody edited
    # `wa_opt_out_words` and took it off the list -- inviting a reply the app
    # would then ignore, which is the exact failure the line exists to prevent.
    Template(
        id="wa_followup",
        name="Follow-up: nudge",
        step=1,
        subject="",
        body=(
            "Hi {{first_name}},\n"
            "Nudging my last message, in case it landed in a bad week.\n"
            "\n"
            "Happy to show what {{service_1}} would look like at {{business_name}}, "
            "or to hear it is not a priority."
        ),
    ),
]


# The shipped copy under the name the rest of the app reads. Same relationship
# `TEMPLATES` has to `BUILTIN_TEMPLATES` on the email side: whatever asks for
# `WA_TEMPLATES` is asking about what ships, and `all_templates` is what answers
# "including whatever the user has written since".
WA_TEMPLATES: list[Template] = WA_BUILTIN_TEMPLATES


# ── User template store ──

# A second file beside `templates.json`, never the same one. Two channels in one
# store means a WhatsApp template with a sixty-word body and no subject can be
# picked for an email campaign, and an id can collide across the two -- an edit
# to "gap_direct" silently becoming an override of the wrong channel's copy.
WA_STORE_NAME = "wa_templates.json"

# Blank means "beside the email store, wherever that currently is". Set it to
# force a location.
WA_TEMPLATES_PATH: str = ""


def store_path() -> str:
    """Where this store lives, resolved now rather than captured at import.

    Derived from `core.templates.TEMPLATES_PATH` so the redirect the test suite
    already performs moves this store too. A second global captured at import
    would have to be redirected a second time, by every caller who remembered,
    and the one that forgot would be writing into a real user's profile during a
    UI test. That has happened in this project once already, and it cost a real
    user their shipped copy.
    """
    if WA_TEMPLATES_PATH:
        return WA_TEMPLATES_PATH
    directory = os.path.dirname(_t.TEMPLATES_PATH) or "."
    return os.path.join(directory, WA_STORE_NAME)


_STORE_VERSION = 1

# (path, stat stamp, templates), for the reason the email store caches: this is
# read once per queued message and the alternative is a file open per send. Its
# own cache, because its own file: sharing one with `core.templates` would make
# an email edit invalidate WhatsApp copy and the reverse.
_CACHE: tuple | None = None


def _forget_store() -> None:
    global _CACHE
    _CACHE = None


def _write_store(templates) -> bool:
    """Replace the store with `templates`, atomically. False when it could not.

    Temp file then `os.replace`, for the reason `core.templates` does it: the
    user's own copy is the one thing in this app that cannot be regenerated, and
    a crash halfway through a write would cost them all of it.
    """
    path = store_path()
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
    """Every WhatsApp template the user has saved. [] when there is none.

    A store hand-edited into invalid JSON costs the user their edits and nothing
    else: the shipped copy is still there and the channel still sends.
    """
    global _CACHE
    path = store_path()
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
    """The shipped copy with the user's edits laid over it, then their own.

    Order is stable -- step, then shipped order within a step, then name -- so a
    picker built from this does not reshuffle when a template is edited.
    """
    try:
        order = {tpl.id: index for index, tpl in enumerate(WA_BUILTIN_TEMPLATES)}
        merged = {tpl.id: tpl for tpl in WA_BUILTIN_TEMPLATES}
        for tpl in load_user_templates():
            merged[tpl.id] = tpl
        return sorted(merged.values(),
                      key=lambda t: (t.step, order.get(t.id, len(order)),
                                     t.name.lower(), t.id))
    except Exception:
        return list(WA_BUILTIN_TEMPLATES)


def get_template(template_id: str) -> Template | None:
    for tpl in all_templates():
        if tpl.id == template_id:
            return tpl
    return None


def templates_for_step(step: int) -> list[Template]:
    return [t for t in all_templates() if t.step == step]


def is_builtin(template_id: str) -> bool:
    return any(tpl.id == template_id for tpl in WA_BUILTIN_TEMPLATES)


def is_overridden(template_id: str) -> bool:
    """Is a shipped template currently being replaced by a user edit?"""
    return is_builtin(template_id) and any(tpl.id == template_id
                                           for tpl in load_user_templates())


def save_user_template(tpl: Template) -> None:
    """Write `tpl` to the WhatsApp store, replacing any entry with the same id."""
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
    """Drop the user's entry for `template_id`. True when there was one."""
    wanted = str(template_id or "").strip()
    stored = load_user_templates()
    kept = [t for t in stored if t.id != wanted]
    if len(kept) == len(stored):
        return False
    return _write_store(kept)


def reset_template(template_id: str) -> Template | None:
    """Drop the override and hand back what it reverts to; None if nothing."""
    delete_user_template(template_id)
    return get_template(template_id)


def new_template_id(name: str, step: int) -> str:
    """An id for a new WhatsApp template: readable, stable, and not taken.

    Checked against the shipped WhatsApp copy and this store only. An id that
    also exists on the email side is not a collision -- the two stores are
    separate files read by separate pickers -- but a new template must never
    silently become an override of a shipped one on its own channel.
    """
    try:
        step = max(0, int(step))
    except (TypeError, ValueError):
        step = 0
    try:
        base = _SLUG_RE.sub("_", str(name or "").lower()).strip("_")[:40].strip("_")
    except Exception:
        base = ""
    base = base or ("wa_custom" if step == 0 else "wa_custom_step%d" % step)
    taken = {t.id for t in WA_BUILTIN_TEMPLATES} | {t.id for t in load_user_templates()}
    if base not in taken:
        return base
    for suffix in range(2, 1000):
        candidate = "%s_%d" % (base, suffix)
        if candidate not in taken:
            return candidate
    return "%s_%s" % (base, hashlib.sha1(base.encode("utf-8", "replace")).hexdigest()[:8])


# ── Rendering ──

# Merge fields that belong in a chat bubble. Not a restriction on what
# `render_wa` will resolve -- it resolves the whole `MERGE_FIELDS` set, because
# a user who reaches for one should get the value and not the machinery -- but
# what the editor should offer, and what the shipped copy stays inside.
WA_MERGE_FIELDS: tuple[str, ...] = (
    "business_name", "first_name", "category", "website_domain",
    "gap_1", "gap_2", "gap_1_evidence",
    "service_1", "service_2", "service_3",
    "ai_opener", "sender_name", "company", "phone",
)

# The rest of the set, each with the reason it does not belong here. Advisory,
# like everything in `validate_wa_template`: nothing is blocked and nothing is
# rewritten.
WA_DISCOURAGED_FIELDS: dict[str, str] = {
    "calendar_link": ("a calendar link asks a stranger to book before they have "
                      "agreed to talk, and on a first message it is the clearest "
                      "tell of an automated blast"),
    "company_website": "a link in a first message from an unknown number",
    "ai_subject": "written to be a subject line, and WhatsApp has no subject",
    "gap_1_subject": "the subject-line phrasing of the gap; use {{gap_1}}",
    "ai_ps": "a P.S. in a chat message reads as an email pasted into WhatsApp",
    "sender_title": "part of an email signature block; the name alone is enough here",
    "postal_address": "footer machinery from the email channel",
    "unsubscribe_line": ("this renders the email opt-out wording; the STOP line is "
                         "added to every first message for you"),
}


def _clip_sentences(text: str, limit: int) -> str:
    """Whole sentences up to `limit` characters, or one clipped sentence.

    A model observation cut mid-clause reads as a broken machine, so sentences
    are taken whole while they fit. Only when the very first one is already over
    budget is it clipped, and then through the same helper the email side uses,
    which cuts on a word boundary and closes the sentence.
    """
    body = str(text or "").strip()
    if len(body) <= limit:
        return body
    kept: list[str] = []
    total = 0
    for piece in _SENTENCE_SPLIT_RE.split(body):
        piece = piece.strip()
        if not piece:
            continue
        extra = len(piece) + (1 if kept else 0)
        if kept and total + extra > limit:
            break
        if not kept and extra > limit:
            return _clip_snippet(piece, limit)
        kept.append(piece)
        total += extra
    return " ".join(kept)


def wa_context(ctx: dict) -> dict:
    """The shared merge context, tightened for a phone.

    Same dictionary `core.templates.build_context` hands the email path, and
    deliberately so: the gap, the services and the model's opener have already
    been resolved once, against the user's own catalogue and through the
    `_observed` guard that discards any model sentence not tied to this
    recipient. Re-deriving any of it here is how the two channels start telling
    one lead two different stories.

    Three fields are changed, and only because sixty words on a phone is not a
    hundred and twenty in an inbox:

    * `ai_opener` is cut to whole sentences inside `WA_AI_OPENER_MAX`. At its
      email length it would be half the message on its own.
    * `ai_ps` is dropped. A postscript is a letter-writing device; in a chat
      thread it reads as an email that was pasted in.
    * `unsubscribe_line` becomes the WhatsApp opt-out. The email wording offers
      a mailbox to write to, and there is no mailbox on this channel.
    """
    out = dict(ctx or {})
    try:
        out["ai_opener"] = _clip_sentences(out.get("ai_opener"), WA_AI_OPENER_MAX)
        out["ai_ps"] = ""
        out["ai_subject"] = ""
        out["unsubscribe_line"] = WA_OPT_OUT_LINE
    except Exception:
        return dict(ctx or {})
    return out


def word_count(text: str) -> int:
    """Words in a message as the reader receives it."""
    return len(str(text or "").split())


def render_wa(template, ctx: dict, opt_out_words=None) -> str:
    """Render a WhatsApp template against `ctx`. One string, nothing else.

    No subject, no HTML alternative, no compliance footer -- the three things
    `core.templates.render` returns that a chat bubble has no place for.

    A message that does not already tell the reader how to stop it gets one
    appended. That is deliberately not a copy convention the shipped templates
    happen to keep: it is applied to whatever the user edited, because a cold
    message with no way out is the one that gets reported, and a report is what
    bans a number for good. A template that teaches the reader to reply STOP in
    its own words is left alone rather than given a second, near-identical
    sentence.

    The chaser gets one too, though the spec only requires it on a first touch.
    Repeating the sentence three days later costs eight words and reads as
    scrupulous rather than as a machine, and it puts the way out at the bottom
    of the *last* message in the thread -- which is the message being read by
    somebody deciding between replying and reporting.

    `opt_out_words` is `settings["wa_opt_out_words"]` when the caller has it.
    Both the check and the line that gets added are made against that
    vocabulary, so a user who edited the list cannot end up sending a message
    that invites a reply their own app will not honour. Omitted, the shipped
    defaults are used and the line says STOP.

    Never raises, and never emits `{{...}}`.
    """
    try:
        entry = _coerce(template)
        if entry is None:
            return ""
        text = _tidy(_resolve(entry.body, wa_context(ctx)))
        if text and not has_opt_out(text, opt_out_words):
            text = text + "\n\n" + opt_out_line(opt_out_words)
        return text
    except Exception:
        return ""


# ── Template validation ──

# Anything that looks like markup. WhatsApp renders none of it: a bold tag
# arrives as the four characters that were typed.
_MARKUP_RE = re.compile(r"</?[a-zA-Z][^>]{0,40}>|&[a-z]{2,8};|&#\d{2,5};")


def validate_wa_template(tpl, ctx: dict | None = None,
                         opt_out_words=None) -> list[dict]:
    """Everything worth knowing about a WhatsApp template, as warnings.

    Returns the same `{"level", "field", "message"}` findings the email editor
    already renders, so the settings screen needs no second panel. It never
    blocks, never rewrites and never refuses: "error" means the reader sees
    something broken, "warning" means it costs a reply or invites a report, and
    both are the user's call.

    Given a `ctx` -- a `core.templates.build_context` result -- the length is
    measured on the message that would actually be sent, opt-out line included.
    Without one it is measured with every merge field standing in as a single
    word, which is a floor and not an estimate: the values only add.
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
                                         "Another WhatsApp template is also called "
                                         "%s, so the picker shows two rows that read "
                                         "the same." % name))

        subject = _raw_field(tpl, "subject")
        if subject.strip():
            findings.append(_finding("warning", "subject",
                                     "WhatsApp has no subject line. Nothing sends "
                                     "this, and the reader never sees it."))

        body = _raw_field(tpl, "body")
        findings.extend(_token_findings(body, "body"))
        findings.extend(_brace_findings(body, "body"))
        if not body.strip():
            return findings + [_finding("error", "body",
                                        "Empty body. Nothing is sent but the opt-out "
                                        "line.")]

        # Length, on the message as it arrives.
        if ctx is not None:
            words = word_count(render_wa(entry, ctx, opt_out_words))
            measured = "as it would be sent to this lead, opt-out line included"
        else:
            padded = _TOKEN_RE.sub("x", body)
            if not has_opt_out(padded, opt_out_words):
                padded = padded + " " + opt_out_line(opt_out_words)
            words = word_count(padded)
            measured = "before the merge fields are filled, and they only add"
        if words >= WA_MAX_WORDS:
            findings.append(_finding("warning", "body",
                                     "%d words %s. Over %d and a phone collapses the "
                                     "message behind Read more, so the first thing a "
                                     "stranger sees is a wall."
                                     % (words, measured, WA_MAX_WORDS)))

        used = _FIELD_RE.findall(body)
        questions = body.count("?")
        if questions > WA_MAX_QUESTIONS:
            findings.append(_finding("warning", "body",
                                     "%d questions. A stranger who asks more than one "
                                     "is running an interview; the reply rate is on "
                                     "the single easiest question." % questions))
        elif questions == 0 and entry.step == 0:
            findings.append(_finding("warning", "body",
                                     "No question. A first message with nothing to "
                                     "answer is read and closed."))

        links = len(_URL_RE.findall(body)) + sum(
            1 for f in used if f in ("calendar_link", "company_website"))
        if links > WA_MAX_LINKS:
            findings.append(_finding("warning", "body",
                                     "%d links. One at most in a chat message from an "
                                     "unknown number." % links))
        if entry.step == 0 and "calendar_link" in used:
            findings.append(_finding("warning", "body",
                                     "{{calendar_link}} on a first message. %s."
                                     % WA_DISCOURAGED_FIELDS["calendar_link"]))

        discouraged = [f for f in WA_DISCOURAGED_FIELDS
                       if f in used and not (f == "calendar_link" and entry.step == 0)]
        for field in discouraged:
            findings.append(_finding("warning", "body",
                                     "{{%s}} belongs to the email channel: %s."
                                     % (field, WA_DISCOURAGED_FIELDS[field])))

        if _MARKUP_RE.search(body):
            findings.append(_finding("warning", "body",
                                     "This looks like markup. WhatsApp renders none "
                                     "of it; the tags arrive as the characters that "
                                     "were typed."))

        # A missing opt-out is not a finding: `render_wa` adds one, and a warning
        # on every correct template is a panel the user stops reading. A *wrong*
        # one is the finding, because it is the failure this line exists to
        # prevent -- the reader types the word they were told to and gets the
        # follow-up anyway, then reports the number.
        taught = _TEACHES_OPT_OUT_RE.search(body)
        if taught and not has_opt_out(body, opt_out_words):
            findings.append(_finding("warning", "body",
                                     "This tells the reader to reply %s, which is not "
                                     "a word the app watches for, so a reply saying it "
                                     "is not honoured and the follow-up still goes. "
                                     "Use one of: %s."
                                     % (taught.group(1).strip(),
                                        ", ".join(opt_out_words or WA_OPT_OUT_WORDS))))

        masked = _mask_opt_out_words(body)
        findings.extend(_shouting_findings(masked, "body"))
        findings.extend(_spam_findings(masked, "body"))
    except Exception:
        return findings
    return findings
