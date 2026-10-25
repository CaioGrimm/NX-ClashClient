import json

from src.emulators.ldplayer import ADB_DEBUG_LOCAL, FARM_RESOLUTION, LDPlayer


class FakeLD(LDPlayer):
    """ldconsole stand-in keeping a list of instances."""

    def __init__(self, tmp_path):
        console = tmp_path / "ldconsole.exe"
        console.write_text("")
        (tmp_path / "vms" / "config").mkdir(parents=True)
        super().__init__(str(console))
        self.rows = {0: ["0", "LDPlayer", "1", "2", "1", "10", "11", "1280", "720", "240"]}
        self.calls = []

    def _run(self, *args):
        self.calls.append(args)
        cmd = args[0]
        if cmd == "list2":
            return "\n".join(",".join(r) for r in self.rows.values())
        if cmd in ("add", "copy"):
            i = max(self.rows) + 1
            self.rows[i] = [str(i), args[2], "0", "0", "0", "-1", "-1", "960", "540", "240"]
        elif cmd == "modify":
            w, h, dpi = args[4].split(",")
            self.rows[int(args[2])][7:10] = [w, h, dpi]
        elif cmd == "launch":
            self.rows[int(args[2])][4] = "1"
        return ""


def test_create_instance_sets_resolution_and_adb(tmp_path):
    ld = FakeLD(tmp_path)
    inst = ld.create_instance("NX-2")
    assert inst.index == 1 and inst.title == "NX-2"
    assert (inst.width, inst.height, inst.dpi) == FARM_RESOLUTION
    assert json.loads(open(ld.config_path(1)).read())["basicSettings.adbDebug"] == ADB_DEBUG_LOCAL
    assert inst.adb_serial() == "127.0.0.1:5557"


def test_copy_instance_keeps_existing_config(tmp_path):
    ld = FakeLD(tmp_path)
    open(ld.config_path(1), "w").write(json.dumps({"basicSettings.width": 960}))
    ld.create_instance("NX-copy", copy_from=0)
    assert ("copy", "--name", "NX-copy", "--from", "0") in ld.calls
    cfg = json.loads(open(ld.config_path(1)).read())
    assert cfg == {"basicSettings.width": 960, "basicSettings.adbDebug": ADB_DEBUG_LOCAL}


def test_launch_staggered_skips_running_and_waits_between(tmp_path):
    ld = FakeLD(tmp_path)
    ld.create_instance("a")
    ld.create_instance("b")
    slept = []
    assert ld.launch_staggered([0, 1, 2], interval=30, sleep=slept.append) == [1, 2]
    assert slept == [30]
