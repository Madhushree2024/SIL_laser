"""Existing workflow tests run as a real authenticated Admin in a test-only DB."""
import pytest
from fastapi.testclient import TestClient
from app.security.accounts import AccountStore
from app.security.access import COOKIE

@pytest.fixture(scope='session')
def regression_account(tmp_path_factory):
    store=AccountStore(tmp_path_factory.mktemp('regression-accounts')/'accounts.sqlite3')
    store.create('regression-admin','Regression password 2026','admin',initial=True)
    token,session=store.login('regression-admin','Regression password 2026')
    return store,token,session

@pytest.fixture(autouse=True)
def authenticate_regression_clients(request,monkeypatch,regression_account):
    if request.node.get_closest_marker('access_control'):
        return
    import app.main as main
    store,token,session=regression_account
    monkeypatch.setitem(main.app.state._state,'account_store',lambda:store)
    original=TestClient.__init__
    def initialize(client,app,*args,**kwargs):
        if app is main.app:
            kwargs.setdefault('base_url','http://127.0.0.1')
        original(client,app,*args,**kwargs)
        if app is main.app:
            client.cookies.set(COOKIE,token)
            client.headers['X-CSRF-Token']=session['csrf_token']
    monkeypatch.setattr(TestClient,'__init__',initialize)


@pytest.fixture(autouse=True)
def isolate_external_lasercad(monkeypatch):
    # Regression tests must never open desktop programs on the developer's machine.
    import app.main as main
    monkeypatch.setattr(main,'open_dxf_in_lasercad',lambda *args:{'status':'not_installed','message':'DXF saved; LaserCAD isolated in test.'})
    monkeypatch.setattr(main.lasercad_controller,'status',lambda **kwargs:{'available':False,'actions':{}})


@pytest.fixture(autouse=True)
def isolate_saved_coordinate_policy(monkeypatch):
    import app.main as main
    from copy import deepcopy
    original=main.load_config
    def generic_config():
        config=deepcopy(original())
        config.get('workspace',{}).pop('coordinate_frame',None)
        return config
    # Dedicated common-origin fixtures explicitly supply their frame policy.
    monkeypatch.setattr(main,'load_config',generic_config)
