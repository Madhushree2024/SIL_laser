"""Authentication and permission boundary around existing application routes."""
from __future__ import annotations

from functools import lru_cache
import hmac
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .accounts import AccountStore, AccessError, SESSION_SECONDS

COOKIE = 'sheath_session'
SAFE = {'GET','HEAD','OPTIONS'}
PUBLIC = {'/api/health','/api/auth/me','/api/auth/setup','/api/auth/login'}
OPERATIONS = {'/api/capture','/api/upload','/api/detect','/api/nest','/api/export','/api/workflow',
              '/api/camera/open','/api/camera/open-machine','/api/camera/close',
              '/api/production/recover','/api/production/retry','/api/project/save',
              '/api/laser/control/open','/api/laser/control/command','/api/laser/control/stop'}


class Credentials(BaseModel):
    username: str = Field(min_length=1,max_length=50)
    password: str = Field(min_length=1,max_length=256)


class NewAccount(Credentials):
    role: Literal['admin','editor','viewer']
    workflow_mode: Literal['automated','manual'] | None = None


class AccountUpdate(BaseModel):
    role: Literal['admin','editor','viewer']
    enabled: bool
    workflow_mode: Literal['automated','manual'] | None = None


class NewPassword(BaseModel):
    password: str = Field(min_length=12,max_length=256)


class PasswordChange(NewPassword):
    current_password: str = Field(min_length=1,max_length=256)


def required_permission(method,path):
    if path.startswith('/api/auth/users') or path=='/api/auth/audit':return 'users'
    if method in SAFE:return 'read'
    if path in {'/api/auth/logout','/api/auth/password'}:return 'read'
    if path.startswith('/api/laser-control/'):
        return 'configure' if path == '/api/laser-control/profiles' else 'operate'
    if path=='/api/config' or path.startswith('/api/calibration/') or path.startswith('/api/laser/alignment') or path=='/api/camera/control':
        return 'configure'
    if path=='/api/templates' or path.startswith('/api/templates/'):return 'templates'
    if path in OPERATIONS:return 'operate'
    # Unknown future write routes never inherit Editor or Read-only access.
    return 'users'


def install_access_control(app,root_getter,machine_getter):
    @lru_cache(maxsize=32)
    def cached_store(path):return AccountStore(Path(path)/'security/accounts.sqlite3')
    app.state.account_store = lambda: cached_store(str(root_getter().resolve()))
    def store():return app.state.account_store()
    router=APIRouter(prefix='/api/auth')

    def state(request):
        session=store().session(request.cookies.get(COOKIE,''))
        return session or {'user':None,'csrf_token':None,'setup_required':not store().configured()}

    def sign_in_response(request,username,password):
        token,data=store().login(username,password,request.client.host if request.client else 'local')
        response=JSONResponse(data)
        response.set_cookie(COOKIE,token,max_age=SESSION_SECONDS,httponly=True,samesite='strict',
                            secure=request.url.scheme=='https',path='/')
        response.headers['Cache-Control']='no-store'
        return response

    @router.get('/me')
    def current_account(request:Request):return state(request)

    @router.post('/setup')
    def setup(request:Request,payload:Credentials):
        store().create(payload.username,payload.password,'admin',initial=True)
        return sign_in_response(request,payload.username,payload.password)

    @router.post('/login')
    def login(request:Request,payload:Credentials):return sign_in_response(request,payload.username,payload.password)

    @router.post('/logout')
    def logout(request:Request):
        store().logout(request.cookies[COOKIE],request.state.identity['user']['username'])
        response=JSONResponse({'ok':True});response.delete_cookie(COOKIE,path='/');return response

    @router.get('/users')
    def users():return {'users':store().users()}

    @router.post('/users')
    def create_user(request:Request,payload:NewAccount):
        return {'user':store().create(payload.username,payload.password,payload.role,request.state.identity['user']['username'],workflow_mode=payload.workflow_mode)}

    @router.patch('/users/{user_id}')
    def update_user(user_id:str,request:Request,payload:AccountUpdate):
        options={'workflow_mode':payload.workflow_mode} if 'workflow_mode' in payload.model_fields_set else {}
        return {'user':store().update(user_id,payload.role,payload.enabled,request.state.identity['user']['username'],**options)}

    @router.delete('/users/{user_id}')
    def delete_user(user_id:str,request:Request):
        current=request.state.identity['user']
        store().delete(user_id,current['id'],current['username'])
        return {'ok':True}

    @router.post('/users/{user_id}/password')
    def reset_password(user_id:str,request:Request,payload:NewPassword):
        current=request.state.identity['user']
        if current['id']==user_id:raise AccessError('Use Change password and enter your current password for your own account.')
        store().reset_password(user_id,payload.password,current['username'])
        return {'ok':True}

    @router.post('/password')
    def change_password(request:Request,payload:PasswordChange):
        current=request.state.identity['user']
        store().reset_password(current['id'],payload.password,current['username'],payload.current_password)
        response=JSONResponse({'ok':True});response.delete_cookie(COOKIE,path='/');return response

    @router.get('/audit')
    def audit():return {'events':store().events()}

    app.include_router(router)

    @app.exception_handler(AccessError)
    async def account_error(request,exc):return JSONResponse({'detail':str(exc)},status_code=exc.status)

    @app.middleware('http')
    async def access_boundary(request,call_next):
        path=request.url.path
        if request.url.hostname not in {'127.0.0.1','localhost','::1'}:
            return JSONResponse({'detail':'Open this application using its local desktop window or localhost address.'},status_code=400)
        protected=path.startswith('/api/') or path.startswith('/data/') or path=='/data'
        try:
            if request.method not in SAFE:
                origin=request.headers.get('origin')
                if origin and urlsplit(origin)._replace(path='',query='',fragment='').geturl().rstrip('/') != str(request.base_url).rstrip('/'):
                    raise AccessError('This action must come from the application window.',403)
            identity=None
            if protected and path not in PUBLIC:
                identity=await run_in_threadpool(lambda:store().session(request.cookies.get(COOKIE,'')))
                if identity is None:raise AccessError('Sign in to use this application.',401)
                request.state.identity=identity
                if required_permission(request.method,path) not in identity['user']['permissions']:
                    await run_in_threadpool(store().audit,identity['user']['username'],'access denied',path,'denied')
                    raise AccessError('Your account does not have permission for this action.',403)
                if identity['user']['role']!='admin' and identity['user'].get('workflow_mode')=='manual' and request.method=='POST' and path=='/api/laser-control/space-cycle/action':
                    raise AccessError('Manual workflow: use the LaserCAD controls after Download Document. Space does not request Start in this mode.',403)
                if identity['user']['role'] in {'editor','viewer'} and request.method=='POST' and path=='/api/camera/open':
                    try:
                        payload=await request.json()
                    except (ValueError,UnicodeError) as exc:
                        raise AccessError('Camera parameters must be valid JSON.',400) from exc
                    machine=await run_in_threadpool(machine_getter)
                    camera=machine['camera'];config=machine['config'].get('camera',{})
                    resolution=config.get('resolution',{})
                    expected={'width':(camera.get('width') or resolution.get('width',1280)) if camera.get('connected') else resolution.get('width',1280),
                              'height':(camera.get('height') or resolution.get('height',720)) if camera.get('connected') else resolution.get('height',720),
                              'fps':round(camera.get('fps') or config.get('fps',30)) if camera.get('connected') else config.get('fps',30)}
                    if not isinstance(payload,dict) or payload.get('allow_non_external') or any(payload.get(key)!=value for key,value in expected.items()):
                        raise AccessError('Only Admin can change camera resolution or frame rate. Reopen the USB camera using its saved settings.',403)
                if request.method not in SAFE:
                    csrf=request.headers.get('x-csrf-token','')
                    if not csrf or not hmac.compare_digest(csrf.encode('utf-8'),identity['csrf_token'].encode('ascii')):
                        raise AccessError('The session could not be verified. Refresh the application and sign in again.',403)
            response=await call_next(request)
            if path == '/api/camera/stream' and identity and response.status_code == 200:
                body = response.body_iterator
                async def session_checked_stream():
                    checked = 0.0
                    try:
                        async for chunk in body:
                            if time.monotonic() - checked >= 1:
                                checked = time.monotonic()
                                valid = await run_in_threadpool(lambda: store().session(request.cookies.get(COOKIE,'')))
                                if valid is None:break
                            yield chunk
                    finally:
                        close = getattr(body,'aclose',None)
                        if close is not None:await close()
                response.body_iterator = session_checked_stream()
            if protected:
                response.headers['Cache-Control']='no-store'
            if identity and request.method not in SAFE and not path.startswith('/api/auth/'):
                try:
                    workflow_id=None
                    if path=='/api/workflow' and response.status_code==202:
                        # Attribute the accepted response, even if another job starts
                        # before this activity record is committed.
                        chunks=[chunk async for chunk in response.body_iterator]
                        payload=b''.join(chunks)
                        async def original_response():
                            yield payload
                        response.body_iterator=original_response()
                        workflow_id=json.loads(payload).get('id')
                    await run_in_threadpool(store().audit,identity['user']['username'],request.method,path,response.status_code,workflow_id)
                except Exception:logging.getLogger(__name__).exception('Could not save activity log')
            return response
        except AccessError as exc:
            return JSONResponse({'detail':str(exc)},status_code=exc.status)
        except (sqlite3.Error, OSError):
            logging.getLogger(__name__).exception('Account storage or access control unavailable')
            return JSONResponse({'detail':'Account storage is unavailable. Check the data folder permissions and application log.'},status_code=503)
