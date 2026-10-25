import pytest

from src.farm.gui import FORM_TABS, fmt_left, from_form, to_form
from src.farm.profile import Profile, ProfileError
from tests import fakes


def test_form_roundtrip_covers_profile_fields():
    p = Profile(name="acc1", emulator_index=0, schedule=["08:00-12:00"], upgrade_priority=["Centro"], min_gold=5)
    values = to_form(p)
    assert values["schedule"] == "08:00-12:00" and values["min_gold"] == "5" and values["auto_upgrade"] is False
    assert from_form(p, values) == p


def test_from_form_parses_and_validates():
    p = Profile(name="acc1", emulator_index=0)
    new = from_form(p, {"min_gold": " 1000 ", "auto_upgrade": True, "schedule": "08:00-12:00, 20:00-23:00",
                        "upgrade_priority": "", "deploy_mode": "test"})
    assert new.min_gold == 1000 and new.auto_upgrade and new.schedule == ["08:00-12:00", "20:00-23:00"]
    assert new.upgrade_priority == [] and new.deploy_mode == "test"
    with pytest.raises(ProfileError):
        from_form(p, {"min_gold": "lots"})
    with pytest.raises(ProfileError):
        from_form(p, {"schedule": "morning"})


def test_form_fields_exist_on_profile():
    from dataclasses import fields
    names = {f.name for f in fields(Profile)}
    assert {n for tab in FORM_TABS.values() for n, _l, _k in tab} <= names


def test_fmt_left():
    assert fmt_left(None) == "-" and fmt_left(65) == "1m05" and fmt_left(3700) == "1h01"


def test_window_runs_a_fake_session(tmp_path):
    tk = pytest.importorskip("tkinter")
    from src.farm.gui import FarmApp
    from src.farm.manager import SessionManager
    from tests.test_farm_worker import FAST
    Profile(name="acc1", emulator_index=0).save(tmp_path)
    mgr = SessionManager(tmp_path, device_factory=fakes.make_fake_device, vision_factory=fakes.make_fake_vision,
                         timings=FAST)
    try:
        app = FarmApp(tmp_path, manager=mgr)
    except tk.TclError:
        pytest.skip("no display")
    try:
        app.max_loops.set("1")
        app.start("acc1")
        import time
        deadline = time.time() + 20
        while mgr.is_running("acc1") and time.time() < deadline:
            app.update()
            time.sleep(0.05)
        app.refresh()
        assert app.cards["acc1"]["state"].cget("text") == "STOPPED"
        assert "loops 1" in app.cards["acc1"]["stats"].cget("text")
        app.form_vars["min_gold"].set("123")
        app.save_selected()
        assert Profile.load("acc1", tmp_path).min_gold == 123
    finally:
        app.on_close()
