"""Keep the test run out of the real user profile.

`core.settings` and `core.templates` both resolve their storage under
`~/.mapharvest` at import time, and `get_template` reads that store on every
call. Without this redirect a test run reads — and a UI test *writes* — the
copy a real user is relying on. That has already happened once: an offscreen
run of the template editor left an override of the shipped `gap_direct`
template with an empty subject and body in a real profile, which renders as a
blank email.

Redirecting the module globals rather than `$HOME` is deliberate: both modules
capture their paths at import, so a patched environment variable arrives too
late to matter.

The redirect is checked rather than trusted. `core.outreach_db` and `core.ai`
resolve their own paths through `settings.SETTINGS_DIR` on every call, so both
follow this one — but they follow it by convention, and a module that started
computing its path at import instead would be silently writing to a real
profile again with nothing to say so. `_isolate_user_profile` now asserts that
every one of the five resolves outside `~/.mapharvest` before a single test
runs.

The header line is here for the other half of the same problem. Every modal in
this app is driven from `tests/test_modals.py` in a child process on a real
platform, because Qt 5.15's offscreen platform aborts on any modal that is
shown; where no real platform exists those tests skip. A skip is invisible in a
`-q` run, and a suite that has quietly stopped opening its dialogs looks exactly
like one that never had them, so the header says which of the two this run is.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import core.settings as _settings
import core.templates as _templates

_REAL_PROFILE = os.path.join(os.path.expanduser("~"), ".leadforge")


def _resolved() -> dict:
    """Every path a test run can write to, asked for now rather than at import."""
    import core.ai as _ai
    import core.outreach_db as _outreach_db

    return {"settings dir": _settings.SETTINGS_DIR,
            "settings file": _settings.SETTINGS_PATH,
            "templates": _templates.TEMPLATES_PATH,
            "outreach db": _outreach_db._default_path(),
            "ai cache": _ai.cache_path()}


@pytest.fixture(autouse=True, scope="session")
def _isolate_user_profile(tmp_path_factory):
    home = tmp_path_factory.mktemp("mapharvest_profile")
    saved = (_settings.SETTINGS_DIR, _settings.SETTINGS_PATH,
             _templates.TEMPLATES_PATH)

    _settings.SETTINGS_DIR = str(home)
    _settings.SETTINGS_PATH = str(home / "settings.json")
    _templates.TEMPLATES_PATH = str(home / "templates.json")

    escaped = {name: path for name, path in _resolved().items()
               if os.path.abspath(path).lower().startswith(_REAL_PROFILE.lower())}
    if escaped:
        raise RuntimeError(
            "the profile redirect did not take: %s still resolves inside %s"
            % (", ".join("%s (%s)" % (name, path)
                         for name, path in sorted(escaped.items())),
               _REAL_PROFILE))
    try:
        yield home
    finally:
        (_settings.SETTINGS_DIR, _settings.SETTINGS_PATH,
         _templates.TEMPLATES_PATH) = saved


def pytest_report_header(config) -> str:
    """Say in one line whether this run can open a dialog."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import test_modals
    except Exception as why:                 # a broken harness is not a broken run
        return "modals: harness unavailable (%s)" % why
    reason = test_modals.platform_available()
    if reason:
        return "modals: SKIPPED — %s" % reason
    return "modals: driven on QT_QPA_PLATFORM=windows in a child process"
