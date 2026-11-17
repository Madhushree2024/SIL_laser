from app.laser_control.job_history import JobHistory
from app.laser_control.dxf_job_manager import DxfJobManager
from test_laser_control_api import drawing


def test_reopen_preserves_file_identity_and_events_without_execution_claims(tmp_path):
    history=JobHistory(tmp_path);manager=DxfJobManager(history,{})
    content=drawing()
    job=manager.receive(content,'first.dxf','upload')
    reopened=JobHistory(tmp_path)
    assert reopened.get(job['id'])['sha256']==job['sha256']
    assert reopened.events(job['id'])[0]['kind']=='received'
    assert reopened.get(job['id'])['execution_seconds'] is None
    assert manager.receive(content,'same-content.dxf','upload')['id']==job['id']
