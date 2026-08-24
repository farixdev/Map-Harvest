"""AI personalisation for outreach copy — Groq and OpenRouter, as cheaply as possible.

The model is the only part of a campaign that costs money per lead, so this
module is built around spending as little as it can:

* **It never sees a web page.** The input is `core.audit.digest()` — roughly 300
  tokens of pre-chewed facts — plus one line naming at most six services from
  the sender profile. Anything that looks like markup is refused outright.
* **It answers once per lead.** Replies are cached on disk at
  `~/.mapharvest/ai_cache.json`, keyed by domain, template and model, so
  re-running a 500-lead campaign after a copy tweak costs nothing at all.
* **It writes three short strings.** subject, opener, ps — the rest of the email
  comes from `core.templates`, which is free.

Groq and OpenRouter both speak the OpenAI chat-completions dialect, so there is
one request builder and a small table of per-provider URL, key and headers.

Nothing here raises, and nothing here is required. Every failure path — no key,
budget spent, provider down, truncated JSON, a stray `{{merge_field}}` in the
answer — returns `ok=False`, and the caller sends the pure template instead. A
bad model reply must never reach a prospect.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from ast import literal_eval

from core import secrets
from core import settings as _settings
from core.settings import ai_budget_left, get_secret, note_ai_tokens

# ── Providers ────────────────────────────────────────────────────────────────

# OpenRouter attributes traffic to an app by these two headers and shows
# "unknown app" without them. They are advertising, not authentication.
_APP_NAME = "MapHarvest"
_APP_URL = "https://mapharvest.app"

_PROVIDERS: dict[str, dict] = {
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "key_setting": "groq_api_key",
        "model_setting": "groq_model",
        "headers": {},
    },
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "key_setting": "openrouter_api_key",
        "model_setting": "openrouter_model",
        "headers": {"HTTP-Referer": _APP_URL, "X-Title": _APP_NAME},
    },
}

# Order tried when `ai_provider` is "auto". Groq first: it is the faster and
# cheaper of the two, and OpenRouter only has to cover the case where Groq has
# no key, no quota or no pulse.
_AUTO_CHAIN = ("groq", "openrouter")

_TIMEOUT = 20.0
_TEST_TIMEOUT = 15.0          # a settings dialog must not hang for the full 20 s
_RETRY_BACKOFF = 2.0
_MAX_REPLY_BYTES = 200_000    # a chat completion this size is a malfunction
_MAX_ERROR_BYTES = 4_000

_UA = f"{_APP_NAME}/1.0 (+{_APP_URL})"

# Rendered in front of the provider's own wording so the settings screen can say
# something useful even when the API returns an empty body.
_STATUS_HINTS = {
    401: "the API key was rejected",
    402: "the provider account is out of credit",
    403: "the API key is not allowed to use this model",
    404: "unknown model name",
    413: "the request was too large",
    429: "rate limited",
}


# ── Prompt ───────────────────────────────────────────────────────────────────

# Fixed wording, ~140 tokens. Every extra sentence here is paid for on every
# lead in every campaign, so it is short on purpose and stays that way.
#
# The last rule is the only one that is also enforced downstream:
# `core.templates._observed` keeps a sentence only when it names the business,
# the domain or the gap, and deletes everything else — a claim about past
# clients or results has nothing of the reader in it and goes. Asking costs a
# dozen tokens and saves the deletion most of the time; the deletion is the
# guarantee, and it does not care how the claim is phrased.
SYSTEM_PROMPT = (
    "You write one-line cold-email personalisation for a business automation "
    'agency. Given a compressed website audit, return JSON with keys "subject", '
    '"opener", "ps". subject: under 55 characters, lowercase-ish, specific to '
    "their business, no hype, no exclamation marks. opener: ONE sentence, max 25 "
    "words, states a concrete thing you noticed on their site — never \"I came "
    "across your website\". ps: ONE short sentence offering a specific automation "
    "tied to the gap. Plain language. No em dashes. No placeholders. Every "
    "sentence must name their business, their domain or the gap you were given: "
    "anything else is deleted before sending, so never write about past clients, "
    "results or ratings."
)

_DIGEST_MAX_CHARS = 1200      # `core.audit.digest` caps at this; trust nothing
_OFFER_MAX_CHARS = 160
_OFFER_MAX_SERVICES = 6

# Three short strings need ~120 tokens. The clamp stops a mistyped setting from
# either truncating every reply mid-word or quietly burning the monthly budget.
_MIN_MAX_TOKENS = 60
_MAX_MAX_TOKENS = 600


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _offer_services(profile: dict) -> list[str]:
    """At most six services to name in the offer line.

    Ranked by `core.templates` when it is importable. That module is the copy
    catalogue and churns far more than this one does; a broken import there
    should cost the offer line its ordering, not stop the campaign.
    """
    services = [str(s).strip() for s in (profile.get("services") or []) if str(s or "").strip()]
    try:
        from core.templates import top_services
    except Exception:
        return services[:_OFFER_MAX_SERVICES]
    return top_services(services, limit=_OFFER_MAX_SERVICES)


def _offer_line(profile: dict) -> str:
    names = _offer_services(profile if isinstance(profile, dict) else {})
    if not names:
        return ""
    line = "OFFER: " + ", ".join(names)
    if len(line) <= _OFFER_MAX_CHARS:
        return line
    return line[:_OFFER_MAX_CHARS].rsplit(",", 1)[0]  # never end mid-service-name


def _user_message(business_name: str, digest: str, profile: dict, tone: str) -> str:
    """The whole of what the model is told about this lead.

    The digest already opens with "SITE: domain | Business Name", so the name is
    repeated only when the digest somehow lacks it — a dozen wasted tokens per
    lead is a real cost at 500 leads.
    """
    parts = []
    name = str(business_name or "").strip()
    if name and name.lower() not in digest.lower():
        parts.append(f"BUSINESS: {name}")
    parts.append(digest)
    offer = _offer_line(profile)
    if offer:
        parts.append(offer)
    parts.append(f"TONE: {str(tone or 'direct').strip()}")
    return "\n".join(parts)


# ── Reply parsing ────────────────────────────────────────────────────────────

_REPLY_KEYS = ("subject", "opener", "ps")
_SUBJECT_MAX_CHARS = 55
_FIELD_MAX_CHARS = 240        # a one-sentence opener that runs longer was not one

_FENCE_RE = re.compile(r"```[A-Za-z]*")
_WS_RE = re.compile(r"\s+")


def _clean_field(value: str) -> str:
    text = _WS_RE.sub(" ", str(value)).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text.rstrip("!").strip()


def _has_placeholder(value: str) -> bool:
    """True when the model handed back template machinery instead of prose.

    `{{name}}` and `[business]` are both common when a model decides to be
    helpful about a fact it was not given. Either one in a live email is worse
    than no personalisation at all.
    """
    return "{{" in value or "[" in value


def _cap_subject(subject: str) -> str:
    """Trim an overlong subject at a word boundary rather than reject the reply.

    A subject five characters over the limit still carries a usable opener and
    ps; throwing the whole reply away to save those characters is a bad trade.
    """
    if len(subject) <= _SUBJECT_MAX_CHARS:
        return subject
    cut = subject[:_SUBJECT_MAX_CHARS].rsplit(" ", 1)[0]
    return (cut or subject[:_SUBJECT_MAX_CHARS]).rstrip(" ,;:-")


def _fields(data) -> dict:
    """Validate a decoded reply (or a cache entry) into the three strings.

    Returns {} on anything doubtful. Used for the cache read as well, because a
    hand-edited cache file must not be able to put a placeholder in an email.
    """
    if not isinstance(data, dict):
        return {}
    out = {}
    for key in _REPLY_KEYS:
        value = data.get(key)
        if not isinstance(value, str):
            return {}
        clean = _clean_field(value)
        if not clean or len(clean) > _FIELD_MAX_CHARS or _has_placeholder(clean):
            return {}
        out[key] = clean
    out["subject"] = _cap_subject(out["subject"])
    return out


def _parse_reply(text: str) -> dict:
    """Model output → the three strings, or {} when it cannot be trusted.

    Models wrap JSON in ``` fences, prepend "Here you go:", append a closing
    remark, and occasionally emit Python-style single quotes. All of that is
    recoverable; a missing key or a placeholder is not.
    """
    if not text:
        return {}
    body = _FENCE_RE.sub(" ", text)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return {}
    blob = body[start:end + 1]

    try:
        data = json.loads(blob)
    except ValueError:
        try:
            data = literal_eval(blob)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            return {}
    return _fields(data)


# ── Disk cache ───────────────────────────────────────────────────────────────

CACHE_FILENAME = "ai_cache.json"
MAX_CACHE_ENTRIES = 5000


def cache_path() -> str:
    """Where the reply cache lives, resolved late.

    `SETTINGS_DIR` is a string, so a module-level `os.path.join` of it froze the
    cache to whatever the profile directory was at first import, and nothing
    could redirect it afterwards — not the test suite, which repoints
    `settings.SETTINGS_DIR`, and not a script. A single personalised lead in
    either then read and rewrote the real user's cache file. Every other store
    in this app resolves its path at call time for exactly this reason.
    """
    return os.path.join(_settings.SETTINGS_DIR, CACHE_FILENAME)


# The audit worker personalises leads across a thread pool, so both the in-process
# copy and the file are guarded. The settings budget counter is guarded too:
# `note_ai_tokens` rewrites settings.json, and two threads doing that at once
# would race.
_cache_lock = threading.Lock()
_budget_lock = threading.Lock()

_cache: dict | None = None    # None = not read from disk yet


def _cache_key(identity: str, template_id: str, model: str) -> str:
    return hashlib.sha1(f"{identity}|{template_id}|{model}".lower().encode("utf-8", "replace")).hexdigest()


def _cache_entries() -> dict:
    """The cache dict, loaded on first use. Caller holds `_cache_lock`."""
    global _cache
    if _cache is not None:
        return _cache
    entries: dict = {}
    try:
        with open(cache_path(), encoding="utf-8") as f:
            data = json.load(f)
        stored = data.get("entries") if isinstance(data, dict) else None
        if isinstance(stored, dict):
            entries = {k: v for k, v in stored.items() if isinstance(v, dict)}
    except (OSError, ValueError):
        entries = {}   # a corrupt cache is a cold cache, never an error
    _cache = entries
    return _cache


def _cache_write(entries: dict) -> None:
    """Persist the cache. Temp file plus `os.replace`, as for settings.json.

    The pid is in the temp name so two MapHarvest windows cannot overwrite each
    other's half-written file.
    """
    target = cache_path()
    tmp = f"{target}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "entries": entries}, f)
        os.replace(tmp, target)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _cache_get(key: str) -> dict:
    with _cache_lock:
        return dict(_cache_entries().get(key) or {})


def _cache_put(key: str, entry: dict) -> None:
    with _cache_lock:
        entries = _cache_entries()
        entries.pop(key, None)     # re-insert at the end: dict order is age order
        entries[key] = entry
        while len(entries) > MAX_CACHE_ENTRIES:
            entries.pop(next(iter(entries)))
        _cache_write(entries)


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _api_error(status: int, body: bytes) -> str:
    """A one-line, human-readable failure. This string reaches the settings UI."""
    detail = ""
    try:
        parsed = json.loads(body.decode("utf-8", "replace"))
        error = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(error, dict):
            detail = str(error.get("message") or "")
        elif isinstance(error, str):
            detail = error
    except ValueError:
        pass
    if not detail:
        detail = _WS_RE.sub(" ", body.decode("utf-8", "replace")).strip()
    hint = _STATUS_HINTS.get(status, "")
    parts = [f"HTTP {status}"] + [p for p in (hint, detail[:200]) if p]
    return ": ".join(parts) if len(parts) > 1 else parts[0]


def _post_json(url: str, headers: dict, payload: dict, timeout: float) -> tuple[dict | None, str, int]:
    """POST JSON and decode JSON back. Returns (parsed, error, http_status).

    Status is 0 for anything that never reached an HTTP response, which is how
    the caller tells "retry this" (429, 5xx) from "the network is down".
    """
    try:
        data = json.dumps(payload).encode("utf-8")
    except (TypeError, ValueError):
        return None, "could not encode the request", 0

    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(_MAX_REPLY_BYTES)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(_MAX_ERROR_BYTES)
        except (OSError, http.client.HTTPException):
            body = b""
        return None, _api_error(exc.code, body), exc.code
    except urllib.error.URLError as exc:
        host = urllib.parse.urlsplit(url).netloc or url
        return None, f"could not reach {host}: {exc.reason}", 0
    except (TimeoutError, http.client.HTTPException, OSError) as exc:
        return None, f"request failed: {exc}", 0

    try:
        parsed = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return None, "the provider returned something that was not JSON", 0
    if not isinstance(parsed, dict):
        return None, "the provider returned an unexpected shape", 0
    return parsed, "", 200


def _chat(provider: str, api_key: str, model: str, messages: list[dict], *,
          max_tokens: int, temperature: float = 0.7, json_mode: bool = True,
          timeout: float = _TIMEOUT) -> tuple[dict | None, str]:
    """One chat completion against either provider. Returns (payload, error).

    Retries at most twice, and only for reasons a retry can fix: once when the
    model rejects JSON mode (support varies per model on OpenRouter, where the
    user picks the model), and once on 429/5xx after a short backoff.
    """
    spec = _PROVIDERS[provider]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": _UA,
    }
    headers.update(spec["headers"])

    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    dropped_json_mode = False
    retried = False
    while True:
        payload, error, status = _post_json(spec["url"], headers, body, timeout)
        if payload is not None:
            return payload, ""
        if (not dropped_json_mode and "response_format" in body
                and status in (400, 422) and "json" in error.lower()):
            # The parser copes with prose-wrapped JSON, so a model without
            # structured-output support is still usable.
            body.pop("response_format")
            dropped_json_mode = True
            continue
        if not retried and (status == 429 or 500 <= status < 600):
            retried = True
            time.sleep(_RETRY_BACKOFF)
            continue
        return None, error


def _reply_text(payload: dict) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, str) else ""


def _usage_tokens(payload: dict, chars: int) -> int:
    """Tokens this call cost, for the monthly budget.

    Some OpenRouter upstreams omit `usage`; four characters per token is the
    usual rule of thumb and the budget only has to be roughly right.
    """
    usage = payload.get("usage")
    if isinstance(usage, dict):
        total = _int(usage.get("total_tokens"))
        if total > 0:
            return total
        split = _int(usage.get("prompt_tokens")) + _int(usage.get("completion_tokens"))
        if split > 0:
            return split
    return max(1, chars // 4)


def _note_tokens(settings: dict, tokens: int) -> None:
    if tokens <= 0:
        return
    with _budget_lock:
        try:
            note_ai_tokens(settings, tokens)
        except OSError:
            pass   # the counter is bookkeeping; a failed write must not lose the reply


# ── Client ───────────────────────────────────────────────────────────────────

def _result(subject: str = "", opener: str = "", ps: str = "", *, provider: str = "",
            model: str = "", tokens: int = 0, cached: bool = False, ok: bool = False) -> dict:
    return {"subject": subject, "opener": opener, "ps": ps, "provider": provider,
            "model": model, "tokens": tokens, "cached": cached, "ok": ok}


def _lead_identity(digest: str, business_name: str) -> str:
    """Stable cache identity for a lead.

    The digest's first line is "SITE: <domain> | <name>", and the domain is the
    natural key. Leads without a site fall back to their name, then to the
    digest itself — never to a shared empty string, which would hand every
    site-less lead the same opener.
    """
    first = digest.splitlines()[0] if digest else ""
    if first[:5].upper() == "SITE:":
        domain = first[5:].split("|", 1)[0].strip().lower()
        if domain:
            return domain
    slug = re.sub(r"[^a-z0-9]+", "-", str(business_name or "").lower()).strip("-")
    return slug or digest


class AIClient:
    """Personalisation over Groq / OpenRouter, holding a live settings dict.

    Everything the client does is driven by that dict — provider choice, keys,
    model names, per-lead token ceiling, monthly budget — so a caller (or a
    test) changes behaviour by changing settings, not by patching the class.
    """

    def __init__(self, settings: dict) -> None:
        self._settings = settings if isinstance(settings, dict) else {}
        self._last_error = ""

    @property
    def last_error(self) -> str:
        """Why the last `personalize` degraded. "" after a success."""
        return self._last_error

    def _chain(self) -> list[tuple[str, str, str]]:
        """[(provider, api_key, model)] to try in order; empty when AI is off."""
        choice = str(self._settings.get("ai_provider") or "auto").strip().lower()
        if choice == "off":
            names: tuple[str, ...] = ()
        elif choice in _PROVIDERS:
            names = (choice,)
        else:
            names = _AUTO_CHAIN

        chain = []
        for name in names:
            spec = _PROVIDERS[name]
            key = get_secret(self._settings, spec["key_setting"]).strip()
            model = str(self._settings.get(spec["model_setting"]) or "").strip()
            if key and model:
                chain.append((name, key, model))
        return chain

    def available(self) -> bool:
        """True when a live call could be made right now.

        Includes the budget: a client with a valid key but no tokens left cannot
        produce anything new, and the caller should not queue work for it.
        Cached leads still personalise — `personalize` checks the cache first.
        """
        return bool(self._chain()) and ai_budget_left(self._settings) > 0

    def personalize(self, *, business_name: str, digest: str, profile: dict,
                    tone: str = "direct", template_id: str = "") -> dict:
        """Three personalised strings for one lead. Never raises.

        `ok=False` means "use the pure template" — no key, budget spent, every
        provider down, or an answer that could not be trusted. The caller does
        not need to know which; `last_error` says, for the log.
        """
        self._last_error = ""
        digest = str(digest or "").strip()[:_DIGEST_MAX_CHARS]
        profile = profile if isinstance(profile, dict) else {}

        if not digest:
            self._last_error = "no audit digest"
            return _result()
        if "</" in digest:
            # A caller passing page HTML would leak the prospect's whole site
            # into the prompt and blow the token budget on the first lead.
            self._last_error = "digest looks like HTML"
            return _result()

        chain = self._chain()
        if not chain:
            self._last_error = "no AI provider configured"
            return _result()

        identity = _lead_identity(digest, business_name)
        template = str(template_id or "").strip().lower()

        # Cache first, across the whole chain: a stored answer costs nothing and
        # is worth returning even when the budget is spent or Groq is down.
        for name, _key, model in chain:
            entry = _cache_get(_cache_key(identity, template, model))
            fields = _fields(entry)
            if fields:
                return _result(**fields, provider=str(entry.get("provider") or name),
                               model=model, cached=True, ok=True)

        if ai_budget_left(self._settings) <= 0:
            self._last_error = "monthly AI token budget spent"
            return _result()

        user = _user_message(business_name, digest, profile, tone)
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user}]
        max_tokens = min(_MAX_MAX_TOKENS, max(_MIN_MAX_TOKENS,
                                              _int(self._settings.get("ai_max_tokens_per_lead"), 220)))

        failures = []
        for name, key, model in chain:
            payload, error = _chat(name, key, model, messages, max_tokens=max_tokens)
            if payload is None:
                failures.append(f"{name}: {error}")
                continue

            text = _reply_text(payload)
            # Charged before parsing: a reply that turns out to be unusable was
            # still billed, and the budget has to reflect that or a broken model
            # can spend the month for free.
            tokens = _usage_tokens(payload, len(SYSTEM_PROMPT) + len(user) + len(text))
            _note_tokens(self._settings, tokens)

            fields = _parse_reply(text)
            if not fields:
                failures.append(f"{name}: unusable reply")
                continue

            _cache_put(_cache_key(identity, template, model),
                       dict(fields, provider=name, model=model))
            return _result(**fields, provider=name, model=model, tokens=tokens, ok=True)

        self._last_error = "; ".join(failures)
        return _result()


# ── Key test ─────────────────────────────────────────────────────────────────

def test_provider(provider: str, api_key: str, model: str) -> tuple[bool, str, int]:
    """One five-token call, for the "Test" button in settings.

    Returns (ok, message, latency_ms). The message is shown to the user as-is,
    so it carries the provider's own wording on failure — "unknown model name"
    and "the API key was rejected" need different fixes.
    """
    name = str(provider or "").strip().lower()
    if name not in _PROVIDERS:
        return False, f"unknown provider: {provider}", 0

    # The settings screen may hand over the stored form; `decrypt` passes
    # plaintext through unchanged, so this is safe either way.
    key = secrets.decrypt(str(api_key or "")).strip()
    model = str(model or "").strip()
    if not key:
        return False, "no API key", 0
    if not model:
        return False, "no model selected", 0

    started = time.perf_counter()
    # No JSON mode and no system prompt: this measures reachability, key and
    # model name for the price of a handful of tokens.
    payload, error = _chat(name, key, model, [{"role": "user", "content": "ping"}],
                           max_tokens=5, temperature=0.0, json_mode=False,
                           timeout=_TEST_TIMEOUT)
    latency_ms = int((time.perf_counter() - started) * 1000)
    if payload is None:
        return False, error, latency_ms
    return True, f"{model} responded", latency_ms


def fetch_models(provider: str, api_key: str, timeout: float = 15.0) -> list[str]:
    """Fetch the list of available models from the provider's API.

    Returns a sorted list of model ID strings, or an empty list on failure.
    """
    name = str(provider or "").strip().lower()
    if name not in _PROVIDERS:
        return []
    key = secrets.decrypt(str(api_key or "")).strip()
    if not key:
        return []

    url = "https://api.groq.com/openai/v1/models" if name == "groq" else "https://openrouter.ai/api/v1/models"
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "User-Agent": _UA,
    }
    if name == "openrouter":
        headers.update(_PROVIDERS["openrouter"]["headers"])

    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(1024 * 1024)
    except Exception:
        return []

    try:
        parsed = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return []

    models = []
    if isinstance(parsed, dict) and isinstance(parsed.get("data"), list):
        for item in parsed["data"]:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                models.append(item["id"])
    return sorted(models)

