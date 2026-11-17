"""Authenticated recording endpoints; no machine-command routes."""
from uuid import UUID
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, ConfigDict, field_validator
from .operator_recording import ACTIONS

class RecordingStart(BaseModel):
    model_config=ConfigDict(extra='forbid')
    notes: str=Field(default='',max_length=2000)
    origin_mode: Literal['unknown','key_origin','machine_zero','current_position','soft_origin']='unknown'
    firmware: str=Field(default='',max_length=100)

class RecordingEvent(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    action: str=Field(max_length=50)
    notes: str=Field(default='',max_length=2000)
    x: float | None=None
    y: float | None=None
    @field_validator('action')
    @classmethod
    def valid_action(cls,value):
        if value not in ACTIONS:raise ValueError('Unknown recording event.')
        return value

class RecordingStop(BaseModel):
    model_config=ConfigDict(extra='forbid')
    session_id: UUID

def create_recording_router(recorder):
    router=APIRouter(prefix='/api/laser-control/recordings',tags=['Operator recording'])
    def actor(request):return request.state.identity['user']['username']
    def call(fn,*args,**kwargs):
        try:return fn(*args,**kwargs)
        except KeyError as exc:raise HTTPException(404,'Recording not found.') from exc
        except (ValueError,OSError) as exc:raise HTTPException(409,str(exc)) from exc

    @router.get('')
    def status():
        return call(recorder.status)

    @router.post('')
    def start(payload:RecordingStart,request:Request):
        return call(recorder.start,actor(request),payload.notes,payload.origin_mode,payload.firmware)

    @router.post('/stop')
    def stop(payload:RecordingStop,request:Request):
        return call(recorder.stop,actor(request),session_id=str(payload.session_id))

    @router.post('/{session_id}/events')
    def event(session_id:UUID,payload:RecordingEvent,request:Request):
        return call(recorder.event,str(session_id),actor(request),**payload.model_dump())

    @router.get('/{session_id}/download')
    def download(session_id:UUID):
        data=call(recorder.download,str(session_id))
        return Response(data,media_type='application/zip',
            headers={'Content-Disposition':f'attachment; filename="operator-recording-{session_id}.zip"',
                     'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
    return router
