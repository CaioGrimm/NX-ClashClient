import io

from src.farm import cli
from src.farm.profile import Profile
from src.farm.worker import State
from tests import fakes
from tests.fakes import FakeDevice, FakeVision
from tests.test_farm_worker import FAST, make


def test_dry_run_reads_home_and_stops():
    dev = FakeDevice("home")
    w, _ = make(device=dev, dry_run=True)
    w.run()
    assert w.history == [State.HOME, State.CHECK_RESOURCES]
    assert w.resources == {"gold": 1, "elixir": 2, "dark": 3}
    assert w.stop_reason == "dry run: resources read"
    assert dev.taps == [] and dev.keys == [] and dev.app_starts == dev.app_stops == 0


def test_dry_run_from_profile_field():
    w, _ = make(profile=Profile(name="acc1", emulator_index=0, dry_run=True), device=FakeDevice("home"))
    w.run()
    assert w.stop_reason == "dry run: resources read"


def test_dry_run_on_search_screen_reads_loot():
    dev = FakeDevice("match")
    w, _ = make(device=dev, vision=FakeVision(enemy_loot=[{"gold": 5, "elixir": 6, "dark": 7}]), dry_run=True)
    w.run()
    assert w.history == [State.HOME, State.RECOVER, State.EVALUATE]
    assert w.last_loot == {"gold": 5, "elixir": 6, "dark": 7}
    assert w.stop_reason == "dry run: loot read"
    assert dev.taps == []


def test_dry_run_unknown_screen_stops_without_recovery():
    dev = FakeDevice("popup")
    w, _ = make(device=dev, dry_run=True)
    w.run()
    assert w.stop_reason == "dry run: not on home or search screen"
    assert dev.taps == [] and dev.keys == [] and dev.app_stops == 0


def test_dry_run_device_drops_inputs():
    dev = FakeDevice("home")
    w, _ = make(device=dev, dry_run=True)
    w.device.tap(0.5, 0.5)
    w.device.key(4)
    w.device.stop_app()
    assert dev.taps == [] and dev.keys == [] and dev.app_stops == 0
    assert [b[0] for b in w.device.blocked] == ["tap", "key", "stop_app"]
    assert w.device.capture() is not None


def test_cli_start_dry_run(tmp_path):
    Profile(name="acc1", emulator_index=0).save(tmp_path)
    out = io.StringIO()
    code = cli.main(["--profiles-dir", str(tmp_path), "start", "acc1", "--dry-run", "--refresh", "0.05"], out=out,
                    device_factory=fakes.make_fake_device, vision_factory=fakes.make_fake_vision, timings=FAST)
    text = out.getvalue()
    assert code == 0
    assert "home resources {'gold': 1, 'elixir': 2, 'dark': 3}" in text
    assert "dry run: resources read" in text


def test_profiles_new_dry_run(tmp_path):
    out = io.StringIO()
    assert cli.main(["--profiles-dir", str(tmp_path), "profiles", "new", "acc1", "--index", "0", "--dry-run"],
                    out=out) == 0
    assert Profile.load("acc1", tmp_path).dry_run


def test_test_deploy_drops_one_troop_and_records(tmp_path):
    dev = FakeDevice("home", battle_frames=3)
    profile = Profile(name="acc1", emulator_index=0, deploy_mode="test")
    w, _ = make(profile=profile, device=dev, capture_dir=tmp_path)
    w.run()
    deploy_taps = dev.taps[dev.taps.index((0.883, 0.889)) + 1:]
    assert len(deploy_taps) == 2  # one card, one drop; nothing after (no return home)
    assert w.attacks == 1 and w.stop_reason == "test deploy: frames recorded"
    frames = list((tmp_path / "acc1").glob("test_*/*.png"))
    assert len(frames) >= 3
