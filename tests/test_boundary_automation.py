from pathlib import Path
import pytest
import app.main as main
from app.laser.lasercad_control import ControlError


class FakeBridge:
    def __init__(self, error=None): self.calls=[]; self.job=None; self.error=error
    def set_job(self, value): self.job=value
    def request(self, action, **kwargs):
        self.calls.append(action)
        if self.error: raise self.error
        return {"requested":True,"action":action}


@pytest.fixture
def framing(tmp_path, monkeypatch):
    path=tmp_path/'job.dxf';path.write_text('exported geometry')
    bridge=FakeBridge()
    config={"lasercad":{"auto_frame_after_dxf":True}}
    monkeypatch.setattr(main,'load_config',lambda:config)
    monkeypatch.setattr(main,'lasercad_controller',bridge)
    monkeypatch.setattr(main,'open_dxf_in_lasercad',lambda *args:{"status":"launch_requested","message":"DXF open","preview":{"status":"refresh_requested"}})
    return {"path":str(path)},bridge,config


def test_run_box_once_after_ready_handoff_never_starts_cutting(framing):
    output,bridge,config=framing
    main._handoff_dxf(output);main._handoff_dxf(output)
    assert bridge.calls==['frame']
    result=output['lasercad']['boundary_frame']
    assert result['status']=='requested' and result['requested']
    assert 'unverified' in result['message']
    assert Path(output['path']).read_text()=='exported geometry'


@pytest.mark.parametrize('setting',[False,None,'true',1])
def test_only_explicit_boolean_true_enables_motion(framing,setting):
    output,bridge,config=framing
    config['lasercad']['auto_frame_after_dxf']=setting
    main._handoff_dxf(output)
    assert bridge.calls==[]


def test_default_does_not_request_motion(framing):
    output,bridge,config=framing
    config['lasercad'].clear()
    main._handoff_dxf(output)
    assert bridge.calls==[] and 'boundary_frame' not in output['lasercad']


@pytest.mark.parametrize('status',['warning','disabled','not_installed','unavailable'])
def test_failed_or_disabled_handoff_never_requests_motion(framing,monkeypatch,status):
    output,bridge,config=framing
    monkeypatch.setattr(main,'open_dxf_in_lasercad',lambda *args:{'status':status,'message':'Not ready'})
    main._handoff_dxf(output)
    assert bridge.calls==[] and output['lasercad']['boundary_frame']['status']=='skipped'


def test_unconfirmed_preview_blocks_automatic_run_box(framing,monkeypatch):
    output,bridge,config=framing
    monkeypatch.setattr(main,'open_dxf_in_lasercad',lambda *args:{'status':'launch_requested','message':'Launch only'})
    main._handoff_dxf(output)
    assert bridge.calls==[]


def test_failed_or_unknown_frame_preserves_export_and_is_not_retried(framing):
    output,bridge,config=framing
    bridge.error=ControlError('Document changed / delivery unknown')
    main._handoff_dxf(output);main._handoff_dxf(output)
    assert bridge.calls==['frame']
    assert output['lasercad']['status']=='launch_requested'
    assert output['lasercad']['boundary_frame']['status']=='warning'
    assert Path(output['path']).exists()


def test_svg_does_not_trigger_lasercad_or_motion(framing,monkeypatch):
    output,bridge,config=framing
    output['path']=str(Path(output['path']).with_suffix('.svg'))
    monkeypatch.setattr(main,'open_dxf_in_lasercad',lambda *args:pytest.fail('SVG must not be sent to LaserCAD'))
    main._handoff_dxf(output)
    assert bridge.calls==[] and 'lasercad' not in output
