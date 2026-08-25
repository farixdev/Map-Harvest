"""Every modal in the app, opened and clicked, in a process that can survive it.

The suite's blind spot has a shape. Qt 5.15's offscreen platform takes an
access violation the moment a QMessageBox is actually shown — a stock box with
no project code in it is enough — so no test in the rest of the suite has ever
opened a confirmation dialog. That is not a gap anybody could see, because a gap
looks like a missing test and this looked like a passing suite. What it hid was
`setCheckBox(QCheckBox("..."))`: PyQt5 5.15 does not transfer ownership on that
setter, the temporary was collected the instant the call returned, and `exec_()`
then dereferenced freed memory on the first Prepare of every fresh install. The
process vanished with no traceback and 673 tests stayed green.

The modal cannot be driven in the test process at all, and the whole design
follows from that. Each case runs in a child with `QT_QPA_PLATFORM=windows`, a
QTimer acts on the dialog once the event loop has it up, and the parent asserts
on the child's exit code and on the JSON line it printed. A segfault is not an
exception the parent can catch; it is a return code — 0xC0000005 = 3221225477 on
Windows — and a return code is something a test can assert on.
`test_harness_detects_a_crash` reintroduces the original temporary on purpose
and requires the child to die of it, so a harness that has quietly stopped
noticing crashes fails instead of passing.

Where no real platform exists — a headless CI box — every driven case skips with
the platform's own error in the reason. Three tests never skip, because the
findings they carry are the ones a headless box would otherwise hide twice over:
`test_every_modal_call_site_is_covered` walks the source and fails when a screen
grows a dialog no case opens, `test_the_covered_map_has_no_stale_entries` fails
when a case outlives its dialog, and
`test_no_temporary_widget_into_a_non_transferring_setter` re-measures which Qt
setters leave an object Python-owned and then forbids the idiom that killed the
process. None of the three needs a window.

The child does its own profile redirect: it is not run under pytest, so
`tests/conftest.py` never executes and nothing else stands between it and
`~/.mapharvest`. `SETTINGS_DIR`, `SETTINGS_PATH`, `TEMPLATES_PATH` and the
outreach database are pointed at a temp directory and asserted to be outside the
real profile before a single screen is imported.
"""

from __future__ import annotations

import ast
import gc
import json
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ── The wire between parent and child ────────────────────────────────────────

# One line, one marker, because Qt writes to stdout on the way past on a real
# platform and none of it may be mistaken for the result.
MARKER = "@@MODAL@@"

# What Windows reports for an access violation. Named rather than compared as
# "non-zero", because the number is the finding: a child that failed to import
# is also non-zero and is a different problem entirely.
ACCESS_VIOLATION = 3221225477

# A dialog that never opens must not hang the suite, so the child gives up on
# its own and says that it did.
DRIVE_TIMEOUT_MS = 12000
CHILD_TIMEOUT_S = 180

_PLATFORM = None
_OWNERSHIP = None

# Where the measurement below parks the widgets it built. Module level and not
# a local, for the reason the measurement exists: half of those objects are
# Python-owned and already sitting in a C++ parent's child list, so letting the
# last Python reference go frees them twice and takes the interpreter with it.
_MEASURED: list = []


# ═══════════════════════════════════════════════════════════════════════════
# PARENT — running one case
# ═══════════════════════════════════════════════════════════════════════════

def _child_env(home: str) -> dict:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "windows"
    env["QT_QPA_FONTDIR"] = os.environ.get(
        "QT_QPA_FONTDIR",
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
    env["MAPHARVEST_MODAL_HOME"] = home
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_case(name: str, **kwargs) -> dict:
    """Open one modal in a child process and bring back what happened."""
    home = tempfile.mkdtemp(prefix="mapharvest-modal-")
    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__), name, json.dumps(kwargs)],
        capture_output=True, text=True, errors="replace",
        timeout=CHILD_TIMEOUT_S, env=_child_env(home), cwd=_ROOT)
    result = {"case": name, "returncode": proc.returncode, "stdout": proc.stdout,
              "stderr": proc.stderr, "report": None}
    for line in proc.stdout.splitlines():
        if line.startswith(MARKER):
            result["report"] = json.loads(line[len(MARKER):])
    return result


def platform_available() -> str:
    """"" when a real window server answered, otherwise why it did not."""
    global _PLATFORM
    if _PLATFORM is None:
        if not sys.platform.startswith("win"):
            _PLATFORM = ("the modal harness needs QT_QPA_PLATFORM=windows and "
                         "this is %s" % sys.platform)
        else:
            probe = run_case("platform")
            report = probe["report"] or {}
            tail = probe["stderr"].strip().splitlines()
            if probe["returncode"] != 0:
                _PLATFORM = ("QT_QPA_PLATFORM=windows would not start (exit %s): "
                             "%s" % (probe["returncode"],
                                     tail[-1] if tail else "no output"))
            elif report.get("platform") != "windows":
                _PLATFORM = ("Qt fell back to the %r platform, which cannot show "
                             "a modal" % report.get("platform"))
            else:
                _PLATFORM = ""
    return _PLATFORM


def _driven(name: str, **kwargs) -> dict:
    """One case's report, with the child's own failure as the message."""
    import pytest

    reason = platform_available()
    if reason:
        pytest.skip("modal harness unavailable: %s" % reason)
    result = run_case(name, **kwargs)
    if result["returncode"] == ACCESS_VIOLATION:
        pytest.fail("%s CRASHED the process (access violation %d)\nstdout:\n%s"
                    % (name, ACCESS_VIOLATION, result["stdout"]))
    assert result["returncode"] == 0, (
        "%s exited %s\nstdout:\n%s\nstderr:\n%s"
        % (name, result["returncode"], result["stdout"], result["stderr"]))
    report = result["report"]
    assert report is not None, "%s printed no report:\n%s" % (name, result["stdout"])
    seen = report.get("seen") or {}
    assert seen.get("opened"), "%s: no modal ever became visible" % name
    assert not seen.get("timed_out"), "%s: the driver gave up on the dialog" % name
    assert not str(seen.get("clicked", "")).startswith("<no "), (
        "%s: %s" % (name, seen.get("clicked")))
    return report


def _labels(seen: dict) -> list:
    return [b["text"] for b in seen.get("buttons") or []]


# ═══════════════════════════════════════════════════════════════════════════
# PARENT — the harness itself
# ═══════════════════════════════════════════════════════════════════════════

def test_harness_reaches_a_real_platform():
    """Name the platform the driven cases run on, so a skip is never silent."""
    import pytest

    reason = platform_available()
    if reason:
        pytest.skip("modal harness unavailable: %s" % reason)
    assert run_case("platform")["report"]["platform"] == "windows"


def test_harness_detects_a_crash():
    """The canary: the original ownership bug, and the child has to die of it.

    Nothing else here proves the harness has teeth. Every driven case asserts
    that a modal behaved, so a mechanism that stopped catching access violations
    would leave all of them passing. This one hands a freshly constructed
    QCheckBox straight to `setCheckBox` — the line that took the app down on
    first Prepare — and requires the return code to say so.
    """
    import pytest

    reason = platform_available()
    if reason:
        pytest.skip("modal harness unavailable: %s" % reason)
    result = run_case("canary_temporary_checkbox")
    assert result["returncode"] != 0, (
        "the temporary-checkbox idiom survived, so this harness can no longer "
        "tell a crash from a pass:\n%s" % result["stdout"])
    assert result["returncode"] == ACCESS_VIOLATION, (
        "expected the access violation %d, got %s\nstderr:\n%s"
        % (ACCESS_VIOLATION, result["returncode"], result["stderr"]))
    assert "SURVIVED" not in result["stdout"]


def test_offscreen_kills_a_stock_message_box():
    """Why this file exists, measured rather than remembered.

    The day Qt stops doing this the whole subprocess apparatus is unnecessary
    and somebody should be told. Until then the platform the rest of the suite
    runs on cannot show a QMessageBox that no project code has touched.
    """
    import pytest

    if not sys.platform.startswith("win"):
        pytest.skip("the offscreen crash is measured on Windows Qt 5.15")
    env = _child_env(tempfile.mkdtemp(prefix="mapharvest-modal-"))
    env["QT_QPA_PLATFORM"] = "offscreen"
    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "stock_message_box", "{}"],
        capture_output=True, text=True, errors="replace",
        timeout=CHILD_TIMEOUT_S, env=env, cwd=_ROOT)
    assert proc.returncode == ACCESS_VIOLATION, (
        "offscreen no longer crashes on a shown QMessageBox (exit %s) — the "
        "subprocess harness in this file can be retired" % proc.returncode)


# ═══════════════════════════════════════════════════════════════════════════
# PARENT — components.confirm()
# ═══════════════════════════════════════════════════════════════════════════

def test_confirm_returns_true_on_the_confirm_button():
    report = _driven("confirm", click="Delete")
    assert report["returned"] is True
    assert report["seen"]["clicked"] == "Delete"
    assert _labels(report["seen"]) == ["Cancel", "Delete"]
    assert report["seen"]["checkbox"] is None


def test_confirm_returns_false_on_cancel():
    report = _driven("confirm", click="Cancel")
    assert report["returned"] is False


def test_confirm_returns_false_on_escape():
    """Escape is the way out, and the way out is never the irreversible one."""
    report = _driven("confirm", key="escape")
    assert report["returned"] is False
    assert report["seen"]["escape"] == "Cancel"
    assert report["seen"]["default"] == "Cancel"
    assert report["seen"]["dismissed_by_escape"] is True


def test_confirm_with_a_remember_key_offers_the_checkbox():
    report = _driven("confirm", click="Delete", remember_key="modal-test")
    assert report["seen"]["checkbox"] == {"text": "Do not ask again",
                                          "checked": False}
    assert report["returned"] is True
    assert report["remembered"] is False


def test_confirm_remembers_a_ticked_box_and_stops_asking():
    report = _driven("confirm_twice", click="Delete", tick=True,
                     remember_key="modal-test")
    assert report["first"] is True
    assert report["remembered"] is True
    assert report["second"] is True
    assert report["second_opened"] is False


def test_confirm_does_not_remember_a_ticked_box_that_was_cancelled():
    """A box ticked on the way to Cancel is not consent to skip the question."""
    report = _driven("confirm", click="Cancel", tick=True,
                     remember_key="modal-test")
    assert report["returned"] is False
    assert report["remembered"] is False


def test_confirm_checkbox_outlives_the_dialog():
    """The ownership contract, read back after exec_ and a forced collection."""
    report = _driven("confirm_box_ownership", click="Delete", tick=True)
    assert report["python_owned"] is True
    assert report["agreed"] is True
    assert report["checkbox_after_exec"] == {"text": "Do not ask again",
                                             "checked": True}


def test_confirm_box_hands_back_a_checkbox_the_caller_has_to_hold():
    """A live trap in `_confirm_box`, pinned by the crash it produces.

    `setCheckBox` does not take Python ownership, so the QCheckBox that comes
    back as the third element of the tuple is kept alive by that name and by
    nothing else. `confirm()` holds it, which is the only reason the shipped
    path is safe; a caller that takes the box and drops the checkbox — the
    obvious thing to write, and what the docstring's "reused" invites — is dead
    at `exec_()` with no traceback.

    The fix is one argument: `QCheckBox("Do not ask again", box)` parents it and
    C++ owns it from the constructor on. When that lands this test fails, and it
    should be inverted to assert the child now survives.
    """
    import pytest

    reason = platform_available()
    if reason:
        pytest.skip("modal harness unavailable: %s" % reason)
    result = run_case("confirm_box_dropped_checkbox", click="Delete")
    assert result["returncode"] == ACCESS_VIOLATION, (
        "_confirm_box no longer crashes when the caller drops the checkbox "
        "(exit %s) — invert this test" % result["returncode"])


# ═══════════════════════════════════════════════════════════════════════════
# PARENT — screen_outreach._ask, the incomplete-profile gate
# ═══════════════════════════════════════════════════════════════════════════

def test_ask_proceeds_without_stopping_the_warning():
    report = _driven("ask", click="Prepare anyway")
    assert report["returned"] == [True, False]
    assert report["seen"]["checkbox"]["text"].startswith("Do not ask again")
    assert _labels(report["seen"]) == ["Open Settings", "Prepare anyway"]
    assert report["seen"]["default"] == "Open Settings"


def test_ask_carries_the_ticked_box_back_to_the_caller():
    """The line that used to be a segfault: `box.checkBox()` after `exec_()`."""
    report = _driven("ask", click="Prepare anyway", tick=True)
    assert report["returned"] == [True, True]


def test_ask_open_settings_means_no():
    report = _driven("ask", click="Open Settings")
    assert report["returned"] == [False, False]


def test_the_profile_gate_swallows_escape_and_stays_on_screen():
    """Measured, and it is a defect: this dialog has no way out but a button.

    `_ask` adds AcceptRole and DestructiveRole and sets no escape button, so
    Qt's own detection finds nothing to map Escape to, and Qt 5's
    `QMessageBox::keyPressEvent` returns without passing Escape up to QDialog.
    Every other confirmation in the app closes on Escape — `confirm()` sets
    Cancel explicitly, and the Yes/No boxes get NoRole for free. This one, the
    first thing a fresh install sees, cannot be dismissed with the key every
    user reaches for.

    A `box.setEscapeButton(settings_btn)` closes it, and this test should then
    be inverted.
    """
    report = _driven("ask", key="escape")
    assert report["seen"]["escape"] == "", (
        "the profile gate grew an escape button — invert this test")
    assert report["seen"]["dismissed_by_escape"] is False


def test_every_other_confirmation_answers_escape():
    """The contrast that makes the gate a defect rather than a house style.

    Eleven dialogs, one key, and every one of them but the gate comes down.
    """
    ignored = [name for name in
               ("confirm", "suppress", "send_for_real", "remove_account",
                "dry_run", "template_delete", "template_reset",
                "template_unsaved", "settings_discard", "settings_leave",
                "store_failed")
               if _driven(name, key="escape")["seen"]["dismissed_by_escape"]
               is not True]
    assert not ignored, "these dialogs ignore Escape: %s" % (ignored,)


def test_profile_gate_from_prepare_writes_the_switch_it_offered():
    """Ticking the box on the gate throws the Settings switch, not a flag."""
    report = _driven("profile_gate", click="Prepare anyway", tick=True,
                     verb="prepare")
    assert report["returned"] is True
    assert report["in_memory"] is False
    assert report["on_disk"] is False
    assert "warn about the sender profile" in report["toast"]


def test_profile_gate_open_settings_sends_the_user_to_settings():
    report = _driven("profile_gate", click="Open Settings", verb="send")
    assert report["returned"] is False
    assert report["settings_signal"] == 1
    assert report["seen"]["title"] == "Send with an unfinished profile?"


def test_profile_gate_lists_every_missing_piece_with_its_cost():
    report = _driven("profile_gate", click="Open Settings", verb="prepare")
    body = report["seen"]["text"]
    for phrase in ("no Gmail account is set up to send from",
                   "your name is missing from the sign-off",
                   "no postal address for the footer",
                   "CAN-SPAM requires one"):
        assert phrase in body, "%r missing from the gate" % phrase


def test_profile_gate_asks_nothing_when_the_check_is_off():
    """Off is warnings-only, and a warning is not a dialog."""
    report = _driven("profile_gate_off", verb="prepare")
    assert report["returned"] is True
    assert report["opened"] is False
    assert "unfinished profile" in report["toast"]


# ═══════════════════════════════════════════════════════════════════════════
# PARENT — suppression, and the undo behind it
# ═══════════════════════════════════════════════════════════════════════════

def test_suppress_confirmation_suppresses_only_when_confirmed():
    report = _driven("suppress", click="Suppress 1 lead")
    assert report["seen"]["title"] == "Never contact 1 lead again?"
    assert report["suppressed_after"] is True
    assert report["toast_action"] == "Undo"


def test_suppress_cancelled_leaves_the_lead_contactable():
    report = _driven("suppress", click="Cancel")
    assert report["suppressed_after"] is False
    assert report["toast_action"] == ""


def test_suppress_undo_puts_the_address_back():
    report = _driven("suppress", click="Suppress 1 lead", undo=True)
    assert report["suppressed_after"] is True
    assert report["suppressed_after_undo"] is False
    assert report["status_after_undo"] == "audited"


def test_suppress_names_the_whole_selection_not_one_row():
    """The finding that made this dialog necessary: fifty rows, one name."""
    report = _driven("suppress", click="Cancel", leads=3)
    assert report["seen"]["title"] == "Never contact 3 leads again?"
    assert "Zeta Roofing" in report["seen"]["informative"]


def test_forgetting_leads_asks_and_says_what_it_is_not():
    """The dialog's whole job is the sentence separating it from Suppress."""
    report = _driven("remove_leads", click="Remove 2 leads")
    assert report["seen"]["title"] == "Forget 2 leads?"
    assert "use Suppress for that" in report["seen"]["informative"]
    assert report["leads_after"] == 0
    assert report["suppressed_after"] is False


def test_forgetting_leads_cancelled_keeps_them():
    report = _driven("remove_leads", click="Cancel")
    assert report["leads_after"] == 2


def test_the_lead_context_menu_opens_and_offers_suppress():
    report = _driven("lead_menu", click="Copy email")
    assert report["seen"]["kind"] == "QMenu"
    assert "Suppress 1 lead (never contact)" in _labels(report["seen"])
    assert "Preview this email" in _labels(report["seen"])
    assert report["clipboard"] == "lead0@example.com"


def test_suppress_from_the_context_menu_survives_the_nested_dialog():
    """The real route to the most destructive action: menu, then confirmation.

    Two blocking loops, one inside the other — the confirmation is raised from
    an action handler running inside `QMenu.exec_()`. Every earlier case reaches
    `_suppress` by calling it, which is not the path a user takes and would not
    notice a nesting that could not survive being shown.
    """
    report = _driven("lead_menu", click="Suppress",
                     then={"click": "Suppress 1 lead"})
    nested = report["seen"]["then"]
    assert nested["opened"] is True
    assert nested["kind"] == "QMessageBox"
    assert nested["title"] == "Never contact 1 lead again?"
    assert report["suppressed_after"] is True


# ═══════════════════════════════════════════════════════════════════════════
# PARENT — Settings: accounts, dry run, templates, discard, leaving
# ═══════════════════════════════════════════════════════════════════════════

def test_account_removal_asks_and_removes():
    report = _driven("remove_account", click="Remove")
    assert report["seen"]["title"] == "Remove rota@example.com?"
    assert "app password is forgotten" in report["seen"]["informative"]
    assert report["rows_after"] == 1
    assert report["toast_action"] == "Undo"


def test_account_removal_cancelled_keeps_the_row():
    report = _driven("remove_account", click="Cancel")
    assert report["rows_after"] == 2


def test_account_removal_undo_restores_the_row_and_its_password():
    """What Google will not show twice has to come back with the row."""
    report = _driven("remove_account", click="Remove", undo=True)
    assert report["rows_after_undo"] == 2
    assert report["emails_after_undo"] == ["rota@example.com",
                                           "second@example.com"]
    assert report["password_after_undo"] == "abcd efgh ijkl mnop"


def test_turning_dry_run_off_asks_before_it_goes_live():
    report = _driven("dry_run", click="Yes")
    assert report["seen"]["title"] == "Turn off dry run?"
    assert "Nothing about a send can be undone" in report["seen"]["text"]
    assert report["seen"]["default"] == "No"
    assert report["dry_run_after"] is False
    assert report["warning_visible"] is True
    assert report["toggle_signals"] == 1


def test_declining_the_dry_run_warning_puts_the_switch_back():
    report = _driven("dry_run", click="No")
    assert report["dry_run_after"] is True
    assert report["warning_visible"] is False
    assert report["toggle_signals"] == 1, (
        "putting the switch back re-entered the handler, so declining the "
        "warning asks again")


def test_unsaved_template_prompt_saves():
    report = _driven("template_unsaved", click="Save")
    assert report["seen"]["title"] == "Unsaved changes"
    assert sorted(_labels(report["seen"])) == ["Cancel", "Discard", "Save"]
    assert report["returned"] is True
    assert report["dirty_after"] is False
    assert report["stored_subject"] == "a subject nobody typed twice"


def test_unsaved_template_prompt_discards():
    report = _driven("template_unsaved", click="Discard")
    assert report["returned"] is True
    assert report["dirty_after"] is False
    assert report["stored_subject"] != "a subject nobody typed twice"


def test_unsaved_template_prompt_cancels_and_the_screen_stays_put():
    report = _driven("template_unsaved", click="Cancel")
    assert report["returned"] is False
    assert report["dirty_after"] is True


def test_settings_discard_prompt_puts_every_field_back():
    report = _driven("settings_discard", click="Discard")
    assert report["seen"]["title"] == "Discard changes?"
    assert report["dirty_after"] is False
    assert report["unsubscribe_after"] == report["unsubscribe_before_edit"]


def test_settings_discard_cancelled_keeps_the_edits():
    report = _driven("settings_discard", click="Cancel")
    assert report["dirty_after"] is True
    assert report["unsubscribe_after"] == "mailto:typed@example.com"


def test_leaving_settings_dirty_offers_save_discard_cancel():
    report = _driven("settings_leave", click="Cancel")
    assert report["seen"]["title"] == "Unsaved changes"
    assert sorted(_labels(report["seen"])) == ["Cancel", "Discard", "Save"]
    assert report["returned"] is False


def test_leaving_settings_and_saving_keeps_the_edit():
    report = _driven("settings_leave", click="Save")
    assert report["returned"] is True
    assert report["dirty_after"] is False
    assert report["unsubscribe_after"] == "mailto:typed@example.com"


def test_leaving_settings_and_discarding_drops_the_edit():
    report = _driven("settings_leave", click="Discard")
    assert report["returned"] is True
    assert report["dirty_after"] is False
    assert report["unsubscribe_after"] != "mailto:typed@example.com"


def test_template_delete_defaults_to_no():
    report = _driven("template_delete", click="No")
    assert report["seen"]["title"] == "Delete template?"
    assert report["seen"]["default"] == "No"
    assert report["templates_after"] == report["templates_before"]


def test_template_delete_on_yes_removes_it():
    report = _driven("template_delete", click="Yes")
    assert report["templates_after"] == report["templates_before"] - 1


def test_template_reset_defaults_to_no():
    report = _driven("template_reset", click="No")
    assert report["seen"]["title"] == "Reset template?"
    assert report["seen"]["default"] == "No"
    assert report["subject_after"] == "an edit that should survive No"


def test_template_reset_on_yes_restores_the_shipped_wording():
    report = _driven("template_reset", click="Yes")
    assert report["subject_after"] != "an edit that should survive No"


def test_the_write_failure_warning_is_reachable_and_dismissable():
    """A one-button wall, and the one thing it must do is come down again."""
    report = _driven("store_failed", click="OK")
    assert report["seen"]["title"] == "Templates could not be written"
    assert _labels(report["seen"]) == ["OK"]
    assert report["status_text"] == "Not saved"


# ═══════════════════════════════════════════════════════════════════════════
# PARENT — the list dialogs on the Input screen
# ═══════════════════════════════════════════════════════════════════════════

def test_domain_list_dialog_saves_what_was_typed():
    report = _driven("list_dialog", click="Save", kind="domain",
                     typed="roofers\n\n  plumbers  \nhvac")
    assert report["seen"]["title"] == "Domain List"
    assert report["items"] == ["roofers", "plumbers", "hvac"]
    assert report["accepted"] is True


def test_domain_list_dialog_cancel_keeps_the_old_list():
    report = _driven("list_dialog", click="Cancel", kind="domain",
                     typed="roofers", start=["cafes"])
    assert report["accepted"] is False
    assert report["items"] == ["cafes"]


def test_area_list_dialog_saves_what_was_typed():
    report = _driven("list_dialog", click="Save", kind="area",
                     typed="Toronto\nMississauga")
    assert report["seen"]["title"] == "Area List"
    assert report["items"] == ["Toronto", "Mississauga"]


def test_the_list_dialog_opens_wider_than_its_own_floor():
    """The fixed 400x320 that could not be dragged, asserted gone."""
    report = _driven("list_dialog", click="Cancel", kind="domain")
    assert report["width"] > report["minimum_width"]
    assert report["maximum_width"] >= 16777215


# ═══════════════════════════════════════════════════════════════════════════
# PARENT — the outreach screen's remaining dialogs
# ═══════════════════════════════════════════════════════════════════════════

def test_the_send_for_real_gate_names_the_count_and_can_be_refused():
    """Refused only. A case that clicks through this one mails real people."""
    report = _driven("send_for_real", click="Cancel")
    assert report["seen"]["title"] == "Mail 2 messages to real businesses?"
    assert "no way to recall a message" in report["seen"]["informative"]
    assert report["worker_started"] is False
    assert report["sending"] is False


def test_a_sent_message_opens_and_closes():
    report = _driven("sent_message", click="Close")
    assert report["seen"]["kind"] == "_SentMailDialog"
    assert report["seen"]["title"] == "Sent message"
    assert "Close" in _labels(report["seen"])


def test_naming_a_view_returns_what_was_typed():
    report = _driven("name_view", click="Save view", typed="Toronto, audited")
    assert report["name"] == "Toronto, audited"


def test_naming_a_view_cancelled_returns_nothing():
    report = _driven("name_view", click="Cancel", typed="Toronto, audited")
    assert report["name"] == ""


def test_the_columns_menu_lists_every_column():
    report = _driven("columns_menu", key="escape")
    assert report["seen"]["kind"] == "QMenu"
    assert "Show every column" in _labels(report["seen"])
    assert "Business" in _labels(report["seen"])
    assert report["fields_after"] == report["fields_before"]


def test_picking_a_column_rebuilds_the_table_inside_the_menus_event_loop():
    """A live crash, pinned by the condition that causes it rather than by luck.

    Picking any entry in the columns menu runs `_set_columns` → `_rebuild_table`
    from inside `QMenu.exec_()`, so the lead table is swapped out and
    `deleteLater`-ed while the popup that asked for it is still on screen and
    Qt is still delivering events to the widget being replaced. Measured on this
    machine: 1 crash in 10 runs driving the action through the harness, 1 in 12
    driving it from the keyboard the way a user does — an access violation,
    exit 3221225477, after the handler has finished. Deferring the same rebuild
    with `QTimer.singleShot(0, ...)` so it runs after the popup has gone
    survived 14 of 14.

    Reproducing that here would be a coin toss, so this asserts the condition
    instead, which is not one: the rebuild is asked for before `exec_()` has
    returned. The fix is to defer it, and this test should then be inverted.
    """
    report = _driven("columns_menu", click="Show every column", intercept=True)
    assert report["fields_after"] > report["fields_before"]
    assert report["rebuilt_inside_exec"] is True, (
        "the columns menu now rebuilds the table after its event loop has "
        "unwound — invert this test")


def test_the_views_menu_opens_and_can_clear_the_filter():
    report = _driven("views_menu", click="Clear the filter")
    assert report["seen"]["kind"] == "QMenu"
    assert "Save this view…" in _labels(report["seen"])
    assert report["search_after"] == ""


def test_the_command_bar_asks_before_arming_a_live_send():
    """Three letters in a command surface must not be able to arm a send."""
    report = _driven("shell_dry_run", click="Turn dry run off")
    assert report["seen"]["title"] == "Send for real?"
    assert report["dry_run_after"] is False
    assert report["on_disk"] is False


def test_the_command_bar_leaves_dry_run_on_when_refused():
    report = _driven("shell_dry_run", click="Cancel")
    assert report["dry_run_after"] is True
    assert report["on_disk"] is True


def test_the_results_context_menu_opens():
    report = _driven("results_menu", click="Copy email")
    assert report["seen"]["kind"] == "QMenu"
    assert "Copy email" in _labels(report["seen"])
    assert report["clipboard"] == "zeta@example.com"


# ═══════════════════════════════════════════════════════════════════════════
# PARENT — anti-rot: no modal may exist without a case
# ═══════════════════════════════════════════════════════════════════════════

# Every place the app can block on a dialog, and the case that opens it. Two
# are named rather than driven: a native file picker is the OS's window, not
# Qt's, and a test that drove it would be testing Explorer.
COVERED = {
    ("ui/app.py", "toggle_dry_run"): "shell_dry_run",
    ("ui/components.py", "confirm"): "confirm",
    ("ui/screen_input.py", "_open_domain_list"): "list_dialog",
    ("ui/screen_input.py", "_open_area_list"): "list_dialog",
    ("ui/screen_input.py", "_browse_export_dir"): "native file picker, OS-owned",
    ("ui/screen_outreach.py", "_ask"): "ask",
    ("ui/screen_outreach.py", "_ask_name"): "name_view",
    ("ui/screen_outreach.py", "_suppress"): "suppress",
    ("ui/screen_outreach.py", "_remove"): "remove_leads",
    ("ui/screen_outreach.py", "_show_lead_menu"): "lead_menu",
    ("ui/screen_outreach.py", "_show_columns_menu"): "columns_menu",
    ("ui/screen_outreach.py", "_show_views_menu"): "views_menu",
    ("ui/screen_outreach.py", "_on_start_clicked"): "send_for_real",
    ("ui/screen_outreach.py", "_open_sent_message"): "sent_message",
    ("ui/screen_outreach.py", "_on_import_csv"): "native file picker, OS-owned",
    ("ui/screen_outreach.py", "_on_export_clicked"): "native file picker, OS-owned",
    ("ui/screen_results.py", "_show_row_menu"): "results_menu",
    ("ui/screen_settings.py", "_offer_to_save"): "template_unsaved",
    ("ui/screen_settings.py", "_offer_to_leave"): "settings_leave",
    ("ui/screen_settings.py", "_store_failed"): "store_failed",
    ("ui/screen_settings.py", "_on_template_delete"): "template_delete",
    ("ui/screen_settings.py", "_on_template_reset"): "template_reset",
    ("ui/screen_settings.py", "_on_discard"): "settings_discard",
    ("ui/screen_settings.py", "_remove_account_row"): "remove_account",
    ("ui/screen_settings.py", "_on_dry_run_toggled"): "dry_run",
}

# What counts as putting something blocking on screen. `exec_` is the whole of
# it for a QDialog and a QMenu; the QMessageBox statics never return without
# showing one, and the QFileDialog getters are the native picker.
_BLOCKING = {"exec_", "exec"}
_STATIC_BOXES = {"question", "warning", "information", "critical", "about"}
_PICKERS = ("QFileDialog", "QInputDialog", "QColorDialog", "QFontDialog")
_SCANNED = ("ui/components.py", "ui/domain_list_dialog.py", "ui/screen_input.py",
            "ui/screen_outreach.py", "ui/screen_results.py",
            "ui/screen_settings.py", "ui/command_palette.py", "ui/app.py")


def _calls_with_scope(tree) -> list:
    """Every Call in `tree`, paired with the function names enclosing it."""
    found = []

    class _Walk(ast.NodeVisitor):
        def __init__(self):
            self.stack = []

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            found.append((node, list(self.stack)))
            self.generic_visit(node)

    _Walk().visit(tree)
    return found


def _owner_name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _modal_sites() -> list:
    """(file, function, what, line) for every call that can block on a dialog."""
    sites = []
    for rel in _SCANNED:
        path = os.path.join(_ROOT, *rel.split("/"))
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), rel)
        for call, stack in _calls_with_scope(tree):
            func = call.func
            if not isinstance(func, ast.Attribute):
                continue
            owner = _owner_name(func.value)
            where = stack[-1] if stack else "<module>"
            what = ""
            if func.attr in _BLOCKING and where != "run":
                what = "%s.exec_" % (owner or "?")
            elif func.attr in _STATIC_BOXES and owner == "QMessageBox":
                what = "QMessageBox.%s" % func.attr
            elif owner in _PICKERS and func.attr.startswith("get"):
                what = "%s.%s" % (owner, func.attr)
            elif func.attr == "confirm" and owner in ("C", "components"):
                what = "components.confirm"
            if what:
                sites.append((rel, where, what, call.lineno))
    return sites


def test_every_modal_call_site_is_covered():
    """No screen may grow a dialog this file has never opened.

    Runs with or without a platform, because the walk is over source and the
    failure it exists to produce — somebody added a confirmation and nobody
    drove it — is exactly what a headless box would otherwise hide.
    """
    uncovered = ["%s:%d %s -> %s" % (rel, line, func, what)
                 for rel, func, what, line in _modal_sites()
                 if (rel, func) not in COVERED]
    assert not uncovered, (
        "modal call sites with no case in tests/test_modals.py:\n  "
        + "\n  ".join(uncovered))


def test_the_covered_map_has_no_stale_entries():
    """A case for a dialog that no longer exists is a case nobody is running."""
    live = {(rel, func) for rel, func, _what, _line in _modal_sites()}
    stale = sorted(key for key in COVERED if key not in live)
    assert not stale, "COVERED names call sites that are gone: %s" % (stale,)


# ═══════════════════════════════════════════════════════════════════════════
# PARENT — the ownership rule that caused the crash
# ═══════════════════════════════════════════════════════════════════════════

def _measure_ownership() -> dict:
    """Ask sip, per sink, whether Python still owns what was handed over.

    Nothing here is shown, so it is safe on the platform the rest of the suite
    runs on. Python still owning the object is the defect condition: the C++
    side holds a pointer to something the interpreter is free to free, and the
    two cases read identically at a call site — `QVBoxLayout.addWidget`
    transfers and `QMessageBox.setCheckBox` does not.
    """
    global _OWNERSHIP
    if _OWNERSHIP is not None:
        return _OWNERSHIP

    from PyQt5 import sip
    from PyQt5.QtCore import QStringListModel
    from PyQt5.QtGui import QIntValidator
    from PyQt5.QtWidgets import (
        QAction, QApplication, QCheckBox, QComboBox, QCompleter,
        QGraphicsBlurEffect, QLineEdit, QMenu, QMessageBox, QPushButton,
        QScrollArea, QStackedWidget, QStyledItemDelegate, QTableView,
        QToolButton, QVBoxLayout, QWidget,
    )

    _MEASURED.append(QApplication.instance() or QApplication([]))
    answers = {}

    # One hidden root under everything, so this leaves no stray top-level
    # windows behind for the focus tests two modules further down the run to
    # trip over.
    root = QWidget()
    _MEASURED.append(root)

    def _sink(label, host, obj, apply):
        _MEASURED.append((host, obj))
        apply(host, obj)
        answers[label] = sip.ispyowned(obj)

    _sink("setCheckBox", QMessageBox(root), QCheckBox("x"),
          lambda h, o: h.setCheckBox(o))
    # The hosts get the root; the objects handed to a sink never do, or the
    # measurement would be of the parent's ownership rather than the sink's.
    _sink("setMenu", QPushButton(root), QMenu(), lambda h, o: h.setMenu(o))
    _sink("setMenu", QToolButton(root), QMenu(), lambda h, o: h.setMenu(o))
    _sink("setValidator", QLineEdit(root), QIntValidator(0, 9),
          lambda h, o: h.setValidator(o))
    _sink("setCompleter", QLineEdit(root), QCompleter(["a"]),
          lambda h, o: h.setCompleter(o))
    _sink("setModel", QComboBox(root), QStringListModel(["a"]),
          lambda h, o: h.setModel(o))
    _sink("setItemDelegate", QTableView(root), QStyledItemDelegate(),
          lambda h, o: h.setItemDelegate(o))
    _sink("setItemDelegateForColumn", QTableView(root), QStyledItemDelegate(),
          lambda h, o: h.setItemDelegateForColumn(0, o))
    _sink("setItemDelegateForRow", QTableView(root), QStyledItemDelegate(),
          lambda h, o: h.setItemDelegateForRow(0, o))
    _sink("addAction", QWidget(root), QAction("a"), lambda h, o: h.addAction(o))
    _sink("setLayout", QWidget(root), QVBoxLayout(), lambda h, o: h.setLayout(o))
    _sink("setWidget", QScrollArea(root), QWidget(), lambda h, o: h.setWidget(o))
    _sink("setGraphicsEffect", QWidget(root), QGraphicsBlurEffect(),
          lambda h, o: h.setGraphicsEffect(o))

    host = QWidget(root)
    layout = QVBoxLayout(host)
    _MEASURED.append((host, layout))
    _sink("addWidget", layout, QWidget(), lambda h, o: h.addWidget(o))
    _sink("addWidget", QStackedWidget(root), QWidget(),
          lambda h, o: h.addWidget(o))

    _OWNERSHIP = answers
    gc.collect()
    return answers


def test_setcheckbox_still_leaves_the_checkbox_python_owned():
    """The measurement the rule below rests on, taken rather than assumed.

    If a PyQt5 release ever adds the missing `/Transfer/` to `setCheckBox` this
    fails, and the guard can then be relaxed on evidence instead of on a hunch.
    """
    owned = _measure_ownership()
    assert owned["setCheckBox"] is True
    assert owned["addWidget"] is False, (
        "addWidget stopped transferring ownership, which would make far more "
        "than the modals unsafe")
    assert owned["setLayout"] is False


def _constructed(node) -> str:
    """The class `node` constructs, or "" when it is not obviously a call to one."""
    if not isinstance(node, ast.Call):
        return ""
    name = _owner_name(node.func) if isinstance(node.func, ast.Attribute) else (
        node.func.id if isinstance(node.func, ast.Name) else "")
    head = name.lstrip("_")[:1]
    return name if head.isupper() else ""


def _could_be_parented(node) -> bool:
    """Was the construction handed something that could keep it alive?

    A QObject built with a parent belongs to C++ from the constructor onward, so
    the temporary is not a temporary. Any name or attribute in the argument list
    could be that parent — `QCheckBox("t", self)`, `_BadgeDelegate(fn, table)` —
    while a call whose arguments are all literals and lambdas has nothing that
    could be one. The rule errs towards silence deliberately: it exists to catch
    `setCheckBox(QCheckBox("Do not ask again"))`, and a false alarm on a line
    that is fine is how a guard gets switched off.
    """
    for arg in list(node.args) + [kw.value for kw in node.keywords]:
        if isinstance(arg, (ast.Name, ast.Attribute)):
            return True
    return False


def test_no_temporary_widget_into_a_non_transferring_setter():
    """The idiom that killed the process, forbidden by a walk over the source.

    `box.setCheckBox(QCheckBox("Do not ask again"))` is one expression and one
    access violation: the object is Python-owned, the only Python reference to
    it is the argument slot, and the slot is gone before `exec_()` runs. Bound
    to a name, or given a parent, it lives as long as the dialog does.

    Needs no platform and no window, so this finding survives a machine that
    cannot show a dialog at all — which is the machine it was written for.
    """
    unsafe = {sink for sink, owned in _measure_ownership().items() if owned}
    assert "setCheckBox" in unsafe

    offences = []
    for rel in _SCANNED:
        path = os.path.join(_ROOT, *rel.split("/"))
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), rel)
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            if not isinstance(func, ast.Attribute) or func.attr not in unsafe:
                continue
            for arg in call.args:
                built = _constructed(arg)
                if built and not _could_be_parented(arg):
                    offences.append(
                        "%s:%d  %s(%s(...)) — %s does not take Python "
                        "ownership, so the temporary is freed before the "
                        "dialog can use it. Bind it to a name, or give the "
                        "constructor a parent."
                        % (rel, call.lineno, func.attr, built, func.attr))
    assert not offences, "\n  ".join([""] + offences)


# ═══════════════════════════════════════════════════════════════════════════
# CHILD
# ═══════════════════════════════════════════════════════════════════════════

# Where the driving timers are parked. A QTimer whose last Python reference is
# a local in the function that started it is collected before it ever fires,
# which is the same mistake this whole file is about.
_ALIVE: list = []


def _isolate() -> str:
    """Point every profile path at a temp directory, before any screen loads.

    The child is not run under pytest, so `tests/conftest.py` never executes and
    nothing else stands between this process and `~/.mapharvest`. Both modules
    capture their paths at import, which is why the globals are rewritten rather
    than the environment, and the outreach database follows `SETTINGS_DIR`
    because it resolves its own path on every call.
    """
    home = os.environ.get("MAPHARVEST_MODAL_HOME") or tempfile.mkdtemp(
        prefix="mapharvest-modal-")
    os.makedirs(home, exist_ok=True)

    import core.settings as _settings
    import core.templates as _templates

    _settings.SETTINGS_DIR = home
    _settings.SETTINGS_PATH = os.path.join(home, "settings.json")
    _templates.TEMPLATES_PATH = os.path.join(home, "templates.json")

    import core.ai as _ai
    import core.outreach_db as _outreach_db

    real = os.path.join(os.path.expanduser("~"), ".mapharvest")
    for path in (_settings.SETTINGS_DIR, _settings.SETTINGS_PATH,
                 _templates.TEMPLATES_PATH, _outreach_db._default_path(),
                 _ai.cache_path()):
        if os.path.abspath(path).lower().startswith(real.lower()):
            raise RuntimeError("profile redirect failed: %s" % path)
    return home


def _child_app():
    """The application, styled the way `ui.app.run` styles it and no less.

    The sheet alone is not the app: `ui.components` writes colours into each
    widget's own stylesheet at build time, so a process that skips `use_theme`
    builds every component in whichever palette it was imported under.
    """
    from PyQt5.QtWidgets import QApplication

    from ui import components as C
    from ui import theme as TH

    app = QApplication.instance() or QApplication(sys.argv[:1])
    TH.apply(app, TH.theme())
    C.use_theme(TH.theme())
    return app


def _label(item) -> str:
    return item.text().replace("&", "")


def _buttons(widget) -> list:
    from PyQt5.QtWidgets import QAbstractButton, QCheckBox

    return [b for b in widget.findChildren(QAbstractButton)
            if not isinstance(b, QCheckBox)]


def _actions(widget) -> list:
    from PyQt5.QtWidgets import QMenu

    return [a for a in widget.actions() if not a.isSeparator()] \
        if isinstance(widget, QMenu) else []


def _blocking_widget():
    """Whatever is holding the event loop: a modal dialog, or a popup menu."""
    from PyQt5.QtWidgets import QApplication

    return QApplication.activeModalWidget() or QApplication.activePopupWidget()


def _record(widget, seen: dict) -> None:
    from PyQt5.QtWidgets import QCheckBox, QMenu, QMessageBox

    seen["opened"] = True
    seen["kind"] = type(widget).__name__
    seen["title"] = widget.windowTitle()
    checkbox = None
    if isinstance(widget, QMessageBox):
        seen["text"] = widget.text()
        seen["informative"] = widget.informativeText()
        seen["buttons"] = [{"text": _label(b), "role": int(widget.buttonRole(b))}
                           for b in widget.buttons()]
        default, escape = widget.defaultButton(), widget.escapeButton()
        seen["default"] = _label(default) if default else ""
        seen["escape"] = _label(escape) if escape else ""
        checkbox = widget.checkBox()
    elif isinstance(widget, QMenu):
        seen["buttons"] = [{"text": _label(a), "role": None}
                           for a in _actions(widget)]
    else:
        seen["buttons"] = [{"text": _label(b), "role": None}
                           for b in _buttons(widget)]
        boxes = widget.findChildren(QCheckBox)
        checkbox = boxes[0] if boxes else None
    if checkbox is not None:
        seen["checkbox"] = {"text": checkbox.text(),
                            "checked": checkbox.isChecked()}


def _drive(*, click: str = "", key: str = "", tick=None, typed: str = "",
           on_open=None, then=None, avoid=None) -> dict:
    """Arm a timer that acts on whatever the next blocking call puts up.

    It polls rather than firing once: a dialog built from a screen can take
    several turns of the event loop to become visible, and a single shot that
    lands early finds nothing and reports a dialog that never opened. The
    deadline is a second timer, because the whole point of this file is that the
    process comes back either way.

    `then` is for the paths that open a dialog out of a dialog — the context
    menu whose Suppress entry raises a confirmation inside the menu's own event
    loop. A second driver is armed *before* the entry is triggered, and told to
    ignore the widget this one is already holding, because the first is not
    always off screen by the time the second's first tick lands.
    """
    from PyQt5.QtCore import Qt, QTimer
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QCheckBox, QMenu, QTextEdit

    seen = {"opened": False, "timed_out": False, "clicked": "", "kind": "",
            "title": "", "text": "", "informative": "", "buttons": [],
            "checkbox": None, "default": "", "escape": "", "then": None}
    state = {"acted": False}

    def act():
        if state["acted"]:
            return
        widget = _blocking_widget()
        if widget is None or not widget.isVisible() or widget is avoid:
            return
        state["acted"] = True
        poll.stop()
        _record(widget, seen)
        if on_open is not None:
            on_open(widget, seen)
        if typed:
            wells = widget.findChildren(QTextEdit)
            if wells:
                wells[0].setPlainText(typed)
        if tick is not None:
            reader = getattr(widget, "checkBox", None)
            box = reader() if callable(reader) else None
            if box is None:
                boxes = widget.findChildren(QCheckBox)
                box = boxes[0] if boxes else None
            if box is not None:
                box.setChecked(bool(tick))
                seen["checkbox"]["checked"] = box.isChecked()
        if key == "escape":
            seen["clicked"] = "<escape>"
            QTest.keyClick(widget, Qt.Key_Escape)
            _after_escape(widget, seen)
            return
        if not click:
            seen["clicked"] = "<no button requested>"
            widget.close()
            return
        for candidate in (_actions(widget) if isinstance(widget, QMenu)
                          else _buttons(widget)):
            if click.lower() in _label(candidate).lower():
                seen["clicked"] = _label(candidate)
                if then:
                    seen["then"] = _drive(avoid=widget, **then)
                if isinstance(widget, QMenu):
                    widget.close()
                    candidate.trigger()
                else:
                    candidate.click()
                return
        seen["clicked"] = "<no button matching %r>" % click
        widget.close()

    def give_up():
        if state["acted"]:
            return
        seen["timed_out"] = True
        widget = _blocking_widget()
        if widget is not None:
            widget.close()

    poll = QTimer()
    poll.setInterval(40)
    poll.timeout.connect(act)
    poll.start()
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(give_up)
    deadline.start(DRIVE_TIMEOUT_MS)
    _ALIVE.extend((poll, deadline))
    return seen


def _after_escape(widget, seen: dict) -> None:
    """Did Escape take the dialog down, or was it ignored?

    Read straight after the key rather than on a later timer, because
    `QDialog::done` hides before it quits the nested loop: the answer is already
    true by the time `keyClick` returns, and a timer would only be waiting for a
    dialog that has stopped existing.

    Qt 5's `QMessageBox::keyPressEvent` returns on Escape without passing the
    key up to QDialog, so a box with no reject-role button swallows it and
    stays open. One left standing is clicked through on its first button, since
    a case that hangs reports nothing at all.
    """
    from PyQt5 import sip

    if sip.isdeleted(widget) or not widget.isVisible():
        seen["dismissed_by_escape"] = True
        return
    seen["dismissed_by_escape"] = False
    remaining = _buttons(widget)
    if remaining:
        seen["clicked"] = "<escape ignored, clicked %s>" % _label(remaining[0])
        remaining[0].click()
    else:
        widget.close()


def _type_into_line(text: str):
    """Fill the first single-line field a dialog carries, once it is up."""
    from PyQt5.QtWidgets import QLineEdit

    def fill(widget, _seen):
        if not text:
            return
        edits = widget.findChildren(QLineEdit)
        if edits:
            edits[0].setText(text)
    return fill


def _toast_text(toaster) -> str:
    from PyQt5.QtWidgets import QLabel

    toasts = toaster.toasts()
    if not toasts:
        return ""
    return " ".join(label.text() for label in toasts[-1].findChildren(QLabel))


def _undo_button(toaster):
    toasts = toaster.toasts()
    if not toasts:
        return None
    return getattr(toasts[-1], "action_button", None)


CASES = {}


def case(name: str):
    def register(fn):
        CASES[name] = fn
        return fn
    return register


# ── Child cases: the harness itself ──────────────────────────────────────────

@case("platform")
def _case_platform(args: dict) -> dict:
    return {"platform": _child_app().platformName(), "seen": {"opened": True}}


@case("stock_message_box")
def _case_stock_message_box(args: dict) -> dict:
    """A QMessageBox with no project code in it, shown. Dies offscreen."""
    from PyQt5.QtWidgets import QMessageBox

    _child_app()
    box = QMessageBox()
    box.setText("stock")
    box.addButton("OK", QMessageBox.AcceptRole)
    seen = _drive(click="OK")
    box.exec_()
    return {"seen": seen}


@case("canary_temporary_checkbox")
def _case_canary(args: dict) -> dict:
    """The original defect, reintroduced on purpose. Must not survive."""
    from PyQt5.QtWidgets import QCheckBox, QMessageBox

    _child_app()
    box = QMessageBox()
    box.setText("canary")
    box.addButton("OK", QMessageBox.AcceptRole)
    box.setCheckBox(QCheckBox("Do not ask again"))
    gc.collect()
    seen = _drive(click="OK")
    box.exec_()
    print("SURVIVED")
    return {"seen": seen}


# ── Child cases: components.confirm ──────────────────────────────────────────

def _confirm_kwargs(args: dict) -> dict:
    return {"title": "Delete template?",
            "body": "Nothing else brings it back.",
            "confirm_text": "Delete", "danger": True,
            "remember_key": args.get("remember_key", "")}


@case("confirm")
def _case_confirm(args: dict) -> dict:
    _child_app()
    from ui import components as C

    C.forget()
    seen = _drive(click=args.get("click", ""), key=args.get("key", ""),
                  tick=args.get("tick"))
    returned = C.confirm(None, **_confirm_kwargs(args))
    return {"seen": seen, "returned": bool(returned),
            "remembered": bool(C.remembered(args.get("remember_key", "")))}


@case("confirm_twice")
def _case_confirm_twice(args: dict) -> dict:
    _child_app()
    from ui import components as C

    C.forget()
    first_seen = _drive(click=args.get("click", ""), tick=args.get("tick"))
    first = C.confirm(None, **_confirm_kwargs(args))
    second_seen = _drive(click="Cancel")
    second = C.confirm(None, **_confirm_kwargs(args))
    return {"seen": first_seen, "first": bool(first), "second": bool(second),
            "second_opened": bool(second_seen["opened"]),
            "remembered": bool(C.remembered(args.get("remember_key", "")))}


@case("confirm_box_ownership")
def _case_confirm_box_ownership(args: dict) -> dict:
    """What `confirm()` itself does: hold all three names across `exec_()`."""
    from PyQt5 import sip

    _child_app()
    from ui import components as C

    box, go, checkbox = C._confirm_box(
        None, "Delete template?", "Nothing else brings it back.", "Delete",
        True, "modal-test")
    owned = sip.ispyowned(checkbox)
    gc.collect()
    seen = _drive(click=args.get("click", ""), tick=args.get("tick"))
    box.exec_()
    gc.collect()
    return {"seen": seen, "python_owned": bool(owned),
            "agreed": box.clickedButton() is go,
            "checkbox_after_exec": {"text": checkbox.text(),
                                    "checked": checkbox.isChecked()}}


@case("confirm_box_dropped_checkbox")
def _case_confirm_box_dropped_checkbox(args: dict) -> dict:
    """The same builder, with the checkbox let go of. Currently fatal."""
    _child_app()
    from ui import components as C

    def build():
        box, _go, _checkbox = C._confirm_box(
            None, "Delete template?", "Nothing else brings it back.", "Delete",
            True, "modal-test")
        return box

    box = build()
    gc.collect()
    seen = _drive(click=args.get("click", ""), key=args.get("key", ""))
    box.exec_()
    print("SURVIVED")
    return {"seen": seen}


# ── Child cases: the outreach screen ─────────────────────────────────────────

def _outreach(*, leads: int = 0, accounts: bool = False, profile: bool = False):
    """A built OutreachScreen over a seeded throwaway store."""
    _child_app()
    from core import outreach_db as DB
    from ui import screen_outreach as SO

    conn = DB.connect()
    for index in range(leads):
        DB.upsert_lead(conn, {
            "email": "lead%d@example.com" % index,
            "name": "Zeta Roofing" if index == 0 else "Firm %d" % index,
            "website": "zetaroofing.example", "opportunity_score": 30,
            "status": "audited", "source": "modal-test"})
    screen = SO.OutreachScreen()
    screen.settings["smtp_accounts"] = [
        {"email": "rota@example.com", "display_name": "Sam Whitfield",
         "enabled": True, "daily_cap": 10}] if accounts else []
    screen.settings["sender_profile"] = {
        "sender_name": "Sam Whitfield",
        "postal_address": "1 Example Rd, Toronto"} if profile else {}
    screen._reload_leads()
    screen.resize(1080, 760)
    screen.show()
    return screen


def _queued_campaign(screen, count: int) -> int:
    from core import outreach_db as DB

    campaign = DB.create_campaign(screen.conn, "modal-test", "gap_direct",
                                  screen.settings.get("sender_profile") or {},
                                  screen.settings)
    for index in range(count):
        DB.queue_message(screen.conn, {
            "campaign_id": campaign, "lead_id": screen._leads[index]["id"],
            "step": 0, "subject": "what went out",
            "body_text": "the body that went out",
            "account_email": "rota@example.com", "scheduled_at": 1.0})
    screen._campaign_id = campaign
    return campaign


@case("ask")
def _case_ask(args: dict) -> dict:
    screen = _outreach()
    seen = _drive(click=args.get("click", ""), key=args.get("key", ""),
                  tick=args.get("tick"))
    go, stop = screen._ask("Prepare with an unfinished profile?",
                           "The sender profile is missing:\n\n  •  everything",
                           "Prepare anyway")
    return {"seen": seen, "returned": [bool(go), bool(stop)]}


@case("profile_gate")
def _case_profile_gate(args: dict) -> dict:
    import core.settings as ST

    screen = _outreach()
    fired = {"settings": 0}
    screen.settings_signal.connect(
        lambda: fired.__setitem__("settings", fired["settings"] + 1))
    seen = _drive(click=args.get("click", ""), key=args.get("key", ""),
                  tick=args.get("tick"))
    returned = screen._profile_gate(args.get("verb", "prepare"))
    return {"seen": seen, "returned": bool(returned),
            "in_memory": bool(screen.settings.get("require_profile_complete",
                                                  True)),
            "on_disk": bool(ST.load_settings().get("require_profile_complete",
                                                   True)),
            "settings_signal": fired["settings"],
            "toast": _toast_text(screen.toaster)}


@case("profile_gate_off")
def _case_profile_gate_off(args: dict) -> dict:
    screen = _outreach()
    screen.settings["require_profile_complete"] = False
    seen = _drive(click="Prepare anyway")
    returned = screen._profile_gate(args.get("verb", "prepare"))
    return {"seen": {"opened": True, "timed_out": False, "clicked": ""},
            "opened": bool(seen["opened"]), "returned": bool(returned),
            "toast": _toast_text(screen.toaster)}


@case("suppress")
def _case_suppress(args: dict) -> dict:
    from core import outreach_db as DB

    count = int(args.get("leads", 1))
    screen = _outreach(leads=count)
    leads = list(screen._leads)[:count]
    seen = _drive(click=args.get("click", ""), key=args.get("key", ""))
    screen._suppress(leads)

    email = leads[0]["email"]
    report = {"seen": seen, "toast_action": "",
              "suppressed_after": bool(DB.is_suppressed(screen.conn, email))}
    undo = _undo_button(screen.toaster)
    if undo is not None:
        report["toast_action"] = undo.text()
        if args.get("undo"):
            undo.click()
            report["suppressed_after_undo"] = bool(
                DB.is_suppressed(screen.conn, email))
            rows = DB.rows(screen.conn,
                           "SELECT status FROM leads WHERE email = ?", (email,))
            report["status_after_undo"] = str(rows[0]["status"]) if rows else ""
    return report


@case("lead_menu")
def _case_lead_menu(args: dict) -> dict:
    """The menu Suppress is reached from, driven to a harmless entry.

    Deliberately not to Suppress itself: triggering it opens a second modal
    inside the first one's event loop, and one driver can only be waiting for
    one dialog. The suppression dialog is covered from the method it calls.
    """
    from PyQt5.QtWidgets import QApplication

    from core import outreach_db as DB

    screen = _outreach(leads=1)
    table = screen.lead_table
    table.selectRow(0)
    QApplication.processEvents()
    pos = table.visualItemRect(table.item(0, 0)).center()
    seen = _drive(click=args.get("click", ""), key=args.get("key", ""),
                  then=args.get("then"))
    screen._show_lead_menu(pos)
    QApplication.processEvents()
    return {"seen": seen, "clipboard": QApplication.clipboard().text(),
            "suppressed_after": bool(DB.is_suppressed(screen.conn,
                                                      "lead0@example.com"))}


@case("remove_leads")
def _case_remove_leads(args: dict) -> dict:
    """Forget, which is the other half of the pair Suppress belongs to."""
    from core import outreach_db as DB

    screen = _outreach(leads=2)
    leads = list(screen._leads)
    seen = _drive(click=args.get("click", ""), key=args.get("key", ""))
    screen._remove(leads)
    return {"seen": seen,
            "leads_after": len(DB.list_leads(screen.conn)),
            "suppressed_after": bool(DB.is_suppressed(screen.conn,
                                                      "lead0@example.com"))}


@case("name_view")
def _case_name_view(args: dict) -> dict:
    _child_app()
    from ui import screen_outreach as SO

    seen = _drive(click=args.get("click", ""), key=args.get("key", ""),
                  on_open=_type_into_line(args.get("typed", "")))
    name = SO._ask_name(None, args.get("current", ""))
    return {"seen": seen, "name": name}


@case("columns_menu")
def _case_columns_menu(args: dict) -> dict:
    """Opened with a column already hidden, or its only useful entry is greyed.

    `intercept` stands in for `_rebuild_table` and records whether the rebuild
    was asked for before `_show_columns_menu` returned — that is, from inside
    the popup's own event loop — without performing it. The real rebuild there
    takes the process down roughly one run in ten, and a test that reproduced
    that would be a coin toss rather than a test; the condition it needs is not
    a coin toss at all.
    """
    screen = _outreach(leads=1)
    screen._set_columns([0])
    before = len(screen._fields)
    watched = {"inside_exec": None, "returned": False}
    if args.get("intercept"):
        def _watch():
            watched["inside_exec"] = not watched["returned"]
        screen._rebuild_table = _watch
    seen = _drive(click=args.get("click", ""), key=args.get("key", ""))
    screen._show_columns_menu()
    watched["returned"] = True
    return {"seen": seen, "fields_before": before,
            "fields_after": len(screen._fields),
            "rebuilt_inside_exec": watched["inside_exec"]}


@case("views_menu")
def _case_views_menu(args: dict) -> dict:
    screen = _outreach(leads=1)
    seen = _drive(click=args.get("click", ""), key=args.get("key", ""))
    screen._show_views_menu()
    return {"seen": seen, "search_after": screen._search}


@case("shell_dry_run")
def _case_shell_dry_run(args: dict) -> dict:
    """The command bar's own safety switch, on the shell and not on a screen."""
    _child_app()
    import core.settings as ST
    from ui import app as APP

    window = APP.MainWindow()
    window.settings["dry_run"] = True
    window.shell.set_dry_run(True)
    window.resize(1080, 760)
    window.show()
    seen = _drive(click=args.get("click", ""), key=args.get("key", ""))
    window.toggle_dry_run()
    return {"seen": seen, "dry_run_after": bool(window.settings.get("dry_run",
                                                                    True)),
            "on_disk": bool(ST.load_settings().get("dry_run", True))}


@case("send_for_real")
def _case_send_for_real(args: dict) -> dict:
    screen = _outreach(leads=2, accounts=True, profile=True)
    _queued_campaign(screen, 2)
    screen.settings["dry_run"] = False
    seen = _drive(click=args.get("click", ""), key=args.get("key", ""))
    screen._on_start_clicked()
    return {"seen": seen, "worker_started": screen.send_worker is not None,
            "sending": bool(getattr(screen, "_sending", False))}


@case("sent_message")
def _case_sent_message(args: dict) -> dict:
    from core import outreach_db as DB

    screen = _outreach(leads=1, accounts=True, profile=True)
    campaign = _queued_campaign(screen, 1)
    message = DB.rows(screen.conn,
                      "SELECT id FROM messages WHERE campaign_id = ?",
                      (campaign,))[0]["id"]
    DB.mark_message(screen.conn, message, status="sent")
    DB.record_transcript(screen.conn, message,
                         "Subject: what went out\r\n\r\nthe body that went out")
    seen = _drive(click=args.get("click", ""), key=args.get("key", ""))
    screen._open_sent_message(message)
    return {"seen": seen}


# ── Child cases: the results screen ──────────────────────────────────────────

@case("results_menu")
def _case_results_menu(args: dict) -> dict:
    from PyQt5.QtWidgets import QApplication

    from ui.screen_results import ResultsScreen

    app = _child_app()
    screen = ResultsScreen()
    screen.setup(["roofers"], ["Toronto"], ["name", "email", "phone", "website"])
    screen.add_table_row({"name": "Zeta Roofing", "email": "zeta@example.com",
                          "phone": "416-555-0100",
                          "website": "zetaroofing.example",
                          "maps_link": "https://maps.example/zeta"})
    screen.resize(1080, 760)
    screen.show()
    QApplication.processEvents()
    pos = screen.table.visualItemRect(screen.table.item(0, 0)).center()
    seen = _drive(click=args.get("click", ""), key=args.get("key", ""))
    screen._show_row_menu(pos)
    QApplication.processEvents()
    return {"seen": seen, "clipboard": app.clipboard().text()}


# ── Child cases: the settings screen ─────────────────────────────────────────

def _settings_screen(*, accounts: int = 0):
    _child_app()
    from ui.screen_settings import SettingsScreen

    screen = SettingsScreen()
    for index in range(accounts):
        screen._add_account_row({
            "email": "rota@example.com" if index == 0 else "second@example.com",
            "display_name": "Sam Whitfield", "enabled": True, "daily_cap": 10})
        screen._account_rows[-1].set_password("abcd efgh ijkl mnop")
    screen.resize(1080, 760)
    screen.show()
    return screen


@case("remove_account")
def _case_remove_account(args: dict) -> dict:
    screen = _settings_screen(accounts=2)
    row = screen._account_rows[0]
    seen = _drive(click=args.get("click", ""), key=args.get("key", ""))
    screen._remove_account_row(row)

    report = {"seen": seen, "rows_after": len(screen._account_rows),
              "toast_action": ""}
    undo = _undo_button(screen.toaster)
    if undo is not None:
        report["toast_action"] = undo.text()
        if args.get("undo"):
            undo.click()
            report["rows_after_undo"] = len(screen._account_rows)
            report["emails_after_undo"] = [r.email() for r in screen._account_rows]
            report["password_after_undo"] = screen._account_rows[0].app_password()
    return report


@case("dry_run")
def _case_dry_run(args: dict) -> dict:
    """Unticked the way a user unticks it, through the signal and not the slot.

    Calling the handler by hand would leave the checkbox in whichever state the
    test put it in and prove nothing about the put-back, which is the half of
    this dialog that has to work: declining the warning has to leave dry run on.
    """
    screen = _settings_screen()
    screen.dry_run_cb.blockSignals(True)
    screen.dry_run_cb.setChecked(True)
    screen.dry_run_cb.blockSignals(False)
    screen.live_warning.hide()

    fired = {"count": 0}
    screen.dry_run_cb.toggled.connect(
        lambda _on: fired.__setitem__("count", fired["count"] + 1))
    seen = _drive(click=args.get("click", ""), key=args.get("key", ""))
    screen.dry_run_cb.setChecked(False)
    return {"seen": seen, "dry_run_after": bool(screen.dry_run_cb.isChecked()),
            "warning_visible": not screen.live_warning.isHidden(),
            "toggle_signals": fired["count"]}


def _open_first_template(screen):
    for row in range(screen.template_list.count()):
        item = screen.template_list.item(row)
        if item is not None:
            screen.template_list.setCurrentItem(item)
            return
    raise RuntimeError("no templates to open")


@case("template_unsaved")
def _case_template_unsaved(args: dict) -> dict:
    import core.templates as TP

    screen = _settings_screen()
    _open_first_template(screen)
    template_id = screen._template_id
    screen.template_subject_edit.setText("a subject nobody typed twice")
    screen._template_dirty = True
    seen = _drive(click=args.get("click", ""), key=args.get("key", ""))
    returned = screen._offer_to_save()
    stored = TP.get_template(template_id)
    return {"seen": seen, "returned": bool(returned),
            "dirty_after": bool(screen._template_dirty),
            "stored_subject": str(getattr(stored, "subject", ""))}


@case("template_delete")
def _case_template_delete(args: dict) -> dict:
    import core.templates as TP

    screen = _settings_screen()
    screen._on_template_new()
    before = len(TP.all_templates())
    seen = _drive(click=args.get("click", ""), key=args.get("key", ""))
    screen._on_template_delete()
    return {"seen": seen, "templates_before": before,
            "templates_after": len(TP.all_templates())}


@case("template_reset")
def _case_template_reset(args: dict) -> dict:
    screen = _settings_screen()
    _open_first_template(screen)
    screen.template_subject_edit.setText("an edit that should survive No")
    screen._save_open_template(quiet=True)
    seen = _drive(click=args.get("click", ""), key=args.get("key", ""))
    screen._on_template_reset()
    return {"seen": seen, "subject_after": screen.template_subject_edit.text()}


@case("store_failed")
def _case_store_failed(args: dict) -> dict:
    screen = _settings_screen()
    seen = _drive(click=args.get("click", ""), key=args.get("key", ""))
    screen._store_failed("saved")
    return {"seen": seen, "status_text": screen.save_status.text()}


@case("settings_discard")
def _case_settings_discard(args: dict) -> dict:
    screen = _settings_screen()
    before = screen.unsubscribe_edit.text()
    screen.unsubscribe_edit.setText("mailto:typed@example.com")
    screen._mark_dirty()
    seen = _drive(click=args.get("click", ""), key=args.get("key", ""))
    screen._on_discard()
    return {"seen": seen, "dirty_after": bool(screen._dirty),
            "unsubscribe_before_edit": before,
            "unsubscribe_after": screen.unsubscribe_edit.text()}


@case("settings_leave")
def _case_settings_leave(args: dict) -> dict:
    screen = _settings_screen()
    screen.unsubscribe_edit.setText("mailto:typed@example.com")
    screen._mark_dirty()
    seen = _drive(click=args.get("click", ""), key=args.get("key", ""))
    returned = screen._offer_to_leave()
    return {"seen": seen, "returned": bool(returned),
            "dirty_after": bool(screen._dirty),
            "unsubscribe_after": screen.unsubscribe_edit.text()}


# ── Child cases: the list dialogs ────────────────────────────────────────────

@case("list_dialog")
def _case_list_dialog(args: dict) -> dict:
    _child_app()
    from ui.domain_list_dialog import DomainListDialog, ListDialog

    start = list(args.get("start") or [])
    if args.get("kind") == "area":
        dialog = ListDialog(start, None, title="Area List",
                            hint="Enter one city/area per line.",
                            placeholder="Toronto")
    else:
        dialog = DomainListDialog(start, None)
    measured = {}

    def measure(widget, _seen):
        measured["width"] = widget.width()
        measured["minimum_width"] = widget.minimumWidth()
        measured["maximum_width"] = widget.maximumWidth()

    seen = _drive(click=args.get("click", ""), typed=args.get("typed", ""),
                  on_open=measure)
    accepted = dialog.exec_() == ListDialog.Accepted
    return {"seen": seen, "accepted": bool(accepted), "items": dialog.items(),
            **measured}


# ── Child entry point ────────────────────────────────────────────────────────

def _child_main(argv: list) -> int:
    _isolate()
    name = argv[1]
    args = json.loads(argv[2]) if len(argv) > 2 else {}
    if name not in CASES:
        raise SystemExit("unknown case %r" % name)
    report = CASES[name](args)
    sys.stdout.write(MARKER + json.dumps(report, default=str) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(_child_main(sys.argv))
