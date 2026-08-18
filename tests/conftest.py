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
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import core.settings as _settings
import core.templates as _templates


@pytest.fixture(autouse=True, scope="session")
def _isolate_user_profile(tmp_path_factory):
    home = tmp_path_factory.mktemp("mapharvest_profile")
    saved = (_settings.SETTINGS_DIR, _settings.SETTINGS_PATH,
             _templates.TEMPLATES_PATH)

    _settings.SETTINGS_DIR = str(home)
    _settings.SETTINGS_PATH = str(home / "settings.json")
    _templates.TEMPLATES_PATH = str(home / "templates.json")
    try:
        yield home
    finally:
        (_settings.SETTINGS_DIR, _settings.SETTINGS_PATH,
         _templates.TEMPLATES_PATH) = saved
