from unittest import mock

from src.emulators.ldplayer import LDPlayer, adb_port, adb_serial, parse_list2

LIST2 = (
    "0,LDPlayer,1311362,1442412,1,12345,12400,960,540,240\r\n"
    "1,Farm 1,0,0,0,-1,-1,1280,720,240\r\n"
    "2,Conta, com virgula,0,0,0,-1,-1,1920,1080,280\r\n"
    "\r\n"
    "lixo qualquer\r\n"
)


def test_parse_list2_fields():
    inst = parse_list2(LIST2)
    assert [i.index for i in inst] == [0, 1, 2]
    first = inst[0]
    assert first.title == "LDPlayer"
    assert first.top_hwnd == 1311362 and first.bind_hwnd == 1442412
    assert first.running is True
    assert first.pid == 12345 and first.vbox_pid == 12400
    assert (first.width, first.height, first.dpi) == (960, 540, 240)
    assert inst[1].running is False and inst[1].pid == -1


def test_parse_list2_title_with_comma():
    assert parse_list2(LIST2)[2].title == "Conta, com virgula"


def test_parse_list2_empty():
    assert parse_list2("") == []


def test_adb_ports():
    assert adb_port(0) == 5555
    assert adb_port(3) == 5561
    assert adb_serial(1) == "127.0.0.1:5557"
    assert adb_serial(2, "win-pc.tailnet.ts.net") == "win-pc.tailnet.ts.net:5559"
    assert parse_list2(LIST2)[1].adb_serial("100.64.0.5") == "100.64.0.5:5557"


def test_ldplayer_commands():
    ld = LDPlayer("ldconsole.exe", adb_host="10.0.0.2")
    with mock.patch("subprocess.run") as run:
        run.return_value = mock.Mock(stdout=LIST2.encode(), returncode=0)
        assert len(ld.list_instances()) == 3
        run.assert_called_with(["ldconsole.exe", "list2"], capture_output=True, timeout=30.0)
        ld.launch(2)
        run.assert_called_with(["ldconsole.exe", "launch", "--index", "2"], capture_output=True, timeout=30.0)
        ld.quit(2)
        run.assert_called_with(["ldconsole.exe", "quit", "--index", "2"], capture_output=True, timeout=30.0)
    assert ld.serial(2) == "10.0.0.2:5559"
