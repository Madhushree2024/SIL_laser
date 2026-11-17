from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.laser import lasercad_control as control


class FakeAPI:
    def __init__(self, exe):
        self.items = [dict(hwnd=1, pid=9, executable=str(exe), title="LaserCAD V8.28.3 - job.dxf")]
        self.children = [dict(hwnd=i + 10, parent=1, control_id=i, text=label,
                              kind="Button", enabled=True, rect=[0, 0, 1, 1])
                         for i, label in enumerate(control.BUTTONS.values())]
        self.clicked = []
        self.sets = []
    def windows(self): return self.items
    def ready(self, window): return True
    def controls(self, window): return self.children
    def click(self, window, button): self.clicked.append(button["text"])
    def set_text(self, window, field, value): self.sets.append(value)


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    exe = tmp_path / "LaserCAD.exe"
    exe.write_bytes(b"fake")
    job = tmp_path / "job.dxf"
    job.write_text("fake geometry")
    monkeypatch.setattr(control, "find_lasercad", lambda *args: exe)
    api = FakeAPI(exe)
    instance = control.LaserCADController(lambda: {}, api=api)
    instance.set_job(job)
    return instance, api


def test_status_never_clicks_and_never_claims_machine_connected(bridge):
    instance, api = bridge
    status = instance.status()
    assert status["available"] and status["actions"]["start"]
    assert status["machine_connected"] is None
    assert api.clicked == []


@pytest.mark.parametrize("action", ["start", "frame", "download"])
def test_wrong_document_blocks_job_commands(bridge, action):
    instance, api = bridge
    api.items[0]["title"] = "LaserCAD V8.28.3 - other.dxf"
    with pytest.raises(control.ControlError, match="latest exported DXF"):
        instance.request(action, confirm_start=True)
    assert not api.clicked


def test_filename_prefix_is_not_a_match(bridge):
    instance, api = bridge
    api.items[0]["title"] = "LaserCAD V8.28.3 - job.dxf.bak"
    assert not instance.status()["job_matches"]


def test_start_requires_confirmation_and_sends_only_one_click(bridge):
    instance, api = bridge
    with pytest.raises(control.ControlError, match="Confirm"):
        instance.request("start")
    assert not api.clicked
    result = instance.request("start", confirm_start=True)
    assert result["requested"] and api.clicked == ["Start"]
    assert "not confirmation" in result["message"]


def test_duplicate_button_or_window_is_rejected(bridge):
    instance, api = bridge
    api.children.append(dict(api.children[0], hwnd=900))
    with pytest.raises(control.ControlError, match="ambiguous"):
        instance.request("frame")
    api.items.append(dict(api.items[0], hwnd=2))
    with pytest.raises(control.ControlError, match="exactly one"):
        instance.request("stop")
    assert not api.clicked


def test_stop_does_not_require_current_job_or_wait_for_command_lock(bridge):
    instance, api = bridge
    instance.set_job(None)
    instance.lock.acquire()
    try:
        assert instance.request("stop")["requested"]
        with pytest.raises(control.ControlError, match="pending"):
            instance.request("x_plus")
    finally:
        instance.lock.release()
    assert api.clicked == ["Stop"]


def test_disabled_and_missing_controls_never_dispatch(bridge):
    instance, api = bridge
    for item in api.children: item["enabled"] = False
    assert not instance.status()["actions"]["x_plus"]
    with pytest.raises(control.ControlError, match="disabled"):
        instance.request("x_plus")
    assert not api.clicked


def test_single_jog_click_has_no_key_hold_or_repetition(bridge):
    instance, api = bridge
    instance.request("x_plus")
    assert api.clicked == ["X+"]


def test_ambiguous_position_mapping_blocks_move_before_changing_fields(bridge):
    instance, api = bridge
    with pytest.raises(control.ControlError, match="uniquely identify"):
        instance.request("move_to", x=10, y=20)
    assert not api.clicked and not api.sets


def test_move_sets_both_verified_fields_then_clicks_once(bridge):
    instance, api = bridge
    for i, axis in enumerate(["X", "Y"]):
        top = i * 40
        api.children.extend([
            dict(hwnd=100+i, parent=2, control_id=100+i, text=axis+"=", kind="Static", enabled=True, rect=[100, top, 120, top+20]),
            dict(hwnd=200+i, parent=2, control_id=200+i, text="0.00", kind="Edit", enabled=True, rect=[125, top, 200, top+20]),
        ])
    instance.request("move_to", x=650, y=370)
    assert api.sets == ["650.000000", "370.000000"] and api.clicked == ["Move to Pos:"]


def test_changed_document_during_request_does_not_click(bridge):
    instance, api = bridge
    original = api.controls
    def changing(window):
        items = original(window)
        api.items[0] = dict(api.items[0], title="LaserCAD - other.dxf")
        return items
    api.controls = changing
    with pytest.raises(control.ControlError, match="changed documents"):
        instance.request("download")
    assert not api.clicked


def test_command_routes_deduplicate_and_stop_bypasses_workflow_lock(bridge, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, 'load_config', lambda: {'laser_control': {'dashboard_enabled': True}})
    instance, api = bridge
    monkeypatch.setattr(main, "lasercad_controller", instance)
    monkeypatch.setattr(main, "WORKFLOW", {"busy": False})
    monkeypatch.setattr(main, "laser_request_ids", {})
    client = TestClient(main.app)
    payload = {"action": "x_plus", "request_id": str(uuid4())}
    assert client.post("/api/laser/control/command", json=payload).status_code == 200
    assert client.post("/api/laser/control/command", json=payload).status_code == 409
    assert api.clicked == ["X+"]
    main.operation_lock.acquire()
    main.WORKFLOW["busy"] = True
    try:
        stop = client.post("/api/laser/control/stop", json={"action": "stop", "request_id": str(uuid4())})
        assert stop.status_code == 200, stop.text
    finally:
        main.operation_lock.release()
    assert api.clicked[-1] == "Stop"


def test_controls_require_operate_permission():
    from app.security.access import required_permission
    for suffix in ["open", "command", "stop"]:
        assert required_permission("POST", "/api/laser/control/" + suffix) == "operate"


def test_unsafe_or_unknown_move_values_do_not_touch_ui(bridge):
    instance, api = bridge
    for x in [None, float("inf"), float("nan"), 100001]:
        with pytest.raises(control.ControlError): instance.request("move_to", x=x, y=0)
    assert not api.clicked and not api.sets


def test_scoped_window_selects_only_matching_drawing_and_rejects_duplicate(bridge):
    instance,api=bridge
    api.items.append(dict(api.items[0],hwnd=2,title='LaserCAD - other.dxf'))
    assert not instance.status()['available']
    assert instance.status(job_scoped=True)['window_id']==1
    instance.request('pause',job_scoped=True)
    assert api.clicked==['Pause/Continue']
    api.items.append(dict(api.items[0],hwnd=3))
    assert not instance.status(job_scoped=True)['available']
    with pytest.raises(control.ControlError): instance.request('start',confirm_start=True,job_scoped=True)
    assert api.clicked==['Pause/Continue']


@pytest.mark.parametrize('changed',[{'window_id':99},{'process_id':99},{'window_title':'other'},{'communication_selection':['COM4']}])
def test_changed_confirmed_native_target_never_clicks(bridge,changed):
    instance,api=bridge
    expected={'window_id':1,'process_id':9,'window_title':api.items[0]['title'],'communication_selection':[],**changed}
    with pytest.raises(control.ControlError,match='confirmed'):
        instance.request('start',confirm_start=True,job_scoped=True,expected_panel=expected)
    assert not api.clicked
