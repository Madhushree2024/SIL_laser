from typing import Literal
from uuid import UUID
import sqlite3
from fastapi import APIRouter, File, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ConfigDict
from .cutting_parameters import CuttingParameters
from .dxf_validator import MAX_FILE_BYTES
from .material_profiles import MaterialProfile


class ParametersRequest(BaseModel):
    revision: int = Field(ge=1)
    parameters: CuttingParameters


class RevisionRequest(BaseModel):
    revision: int = Field(ge=1)


class CommandRequest(BaseModel):
    action: Literal['connect','disconnect','transfer','start','pause','resume','stop']
    job_id: str | None = None


class FallbackJobRequest(BaseModel):
    action: Literal['download', 'frame', 'stop', 'start', 'pause', 'resume']
    job_id: str | None = None
    request_id: UUID
    operator_confirmed: bool = False
    revision: int | None = None
    dxf_sha256: str | None = None
    observed_state: Literal['running', 'paused'] | None = None
    window_title: str | None = None
    communication_selection: list[str] | None = None


class HostViewRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra='forbid')
    action: Literal['show','hide','detach']
    x: float = Field(default=0, ge=0, le=30000)
    y: float = Field(default=0, ge=0, le=30000)
    width: float = Field(default=0, ge=0, le=30000)
    height: float = Field(default=0, ge=0, le=30000)
    viewport_width: float = Field(default=0, ge=0, le=30000)
    viewport_height: float = Field(default=0, ge=0, le=30000)


class LayoutRequest(BaseModel):
    action: Literal['split','restore']


class CycleReviewRequest(BaseModel):
    cycle_id: UUID
    operator_confirmed: bool
    revision: int = Field(ge=1)
    dxf_sha256: str
    window_title: str
    communication_selection: list[str]


class CycleFinishRequest(BaseModel):
    cycle_id: UUID
    outcome: Literal['completed','aborted']
    operator_confirmed: bool
    machine_stopped: bool


def create_router(service):
    router = APIRouter(prefix='/api/laser-control', tags=['Laser Control'])

    def call(function, *args):
        try:
            return function(*args)
        except KeyError as exc:
            raise HTTPException(404, 'Job not found.') from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc
        except sqlite3.Error as exc:
            raise HTTPException(503, 'Laser job storage is unavailable. Pending panel-request outcomes may be unconfirmed; do not retry automatically.') from exc

    @router.get('/status')
    def status():
        return call(service.status)

    @router.get('/lasercad-host')
    def host_status():
        return call(service.host_status)

    @router.post('/lasercad-host')
    def host_view(payload: HostViewRequest):
        return call(service.host_view, payload.model_dump())

    @router.get('/job-controls')
    def controls():
        return call(service.controls_status)

    @router.post('/cycle/review')
    def review_cycle(payload: CycleReviewRequest, request: Request):
        if not payload.operator_confirmed:
            raise HTTPException(409, 'Confirm the downloaded job, actual LaserCAD settings, placement and readiness first.')
        return call(service.review_cycle, str(payload.cycle_id), payload.revision, payload.dxf_sha256,
                    payload.window_title, payload.communication_selection, request.state.identity['user']['username'])

    @router.post('/cycle/confirm')
    def finish_cycle(payload: CycleFinishRequest, request: Request):
        if not payload.operator_confirmed or not payload.machine_stopped:
            raise HTTPException(409, 'Observe the machine and explicitly confirm it is stopped. This action sends no machine command.')
        return call(service.cycles().finish, str(payload.cycle_id), payload.outcome, request.state.identity['user']['username'])

    @router.post('/layout')
    def layout(payload: LayoutRequest):
        return call(service.layout, payload.action)

    @router.get('/jobs')
    def jobs():
        return {'jobs': call(service.recent)}

    @router.get('/jobs/{job_id}')
    def job(job_id: str):
        return call(service.history().get, job_id)

    @router.get('/jobs/{job_id}/events')
    def events(job_id: str):
        call(service.history().get, job_id)
        return {'events': service.history().events(job_id)}

    @router.get('/jobs/{job_id}/file')
    def download(job_id: str):
        value = call(service.history().get, job_id)
        return FileResponse(call(service.manager().path, value), filename=value['filename'], media_type='application/dxf')

    @router.post('/jobs/upload')
    async def upload(file: UploadFile = File(...)):
        call(service.cycles().require_idle)
        content = await file.read(MAX_FILE_BYTES+1)
        return call(service.receive, content, file.filename or 'job.dxf')

    @router.post('/jobs/{job_id}/select')
    def select(job_id: str):
        return call(service.select, job_id)

    @router.post('/jobs/{job_id}/parameters')
    def parameters(job_id: str, payload: ParametersRequest):
        return call(service.update_parameters, job_id, payload.parameters, payload.revision)

    @router.post('/jobs/{job_id}/prepare')
    def prepare(job_id: str, payload: RevisionRequest):
        return call(service.prepare, job_id, payload.revision)

    @router.post('/jobs/{job_id}/validate')
    def validate(job_id: str):
        return call(service.revalidate, job_id)

    @router.post('/fallback-command')
    def fallback_command(payload: FallbackJobRequest):
        if payload.action == 'stop':
            raise HTTPException(400, 'Use the priority fallback-stop endpoint for Stop.')
        return call(service.request_fallback_command, payload.action, payload.job_id, payload.request_id,
                    payload.operator_confirmed, payload.revision, payload.dxf_sha256, payload.observed_state, payload.window_title,
                    payload.communication_selection)

    @router.post('/fallback-stop')
    def fallback_stop(payload: FallbackJobRequest):
        if payload.action != 'stop':
            raise HTTPException(400, 'Only Stop is accepted on this route.')
        return call(service.request_fallback_command, 'stop', payload.job_id, payload.request_id)

    @router.post('/jobs/{job_id}/fallback')
    def fallback(job_id: str):
        return call(service.open_fallback, job_id)

    @router.get('/profiles')
    def profiles():
        return {'profiles': service.profiles().list()}

    @router.post('/profiles')
    def save_profile(payload: MaterialProfile):
        return service.profiles().save(payload)

    @router.post('/command')
    def command(payload: CommandRequest):
        return call(service.command, payload.action, payload.job_id)

    @router.post('/stop')
    def stop(payload: CommandRequest):
        if payload.action != 'stop':
            raise HTTPException(400, 'Only Stop is accepted on this route.')
        return call(service.command, 'stop', payload.job_id)

    return router
