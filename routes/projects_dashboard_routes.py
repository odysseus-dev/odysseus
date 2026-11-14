"""Projects dashboard API: read-only, local browser administrator only (same gate as the provider catalog)."""
import re

from fastapi import APIRouter, HTTPException, Request, Response

from pydantic import BaseModel, Field

from src.application_modes import require_browser_admin, require_local
from src.projects_grants import GrantError, enabled as grants_switch


def _reader(request):
    from core.middleware import require_admin
    require_local(request)
    if getattr(request.state, 'current_user', None) in ('internal-tool', 'api', 'demo', 'system') \
            or 'authorization' in request.headers or 'x-odysseus-internal-token' in request.headers:
        raise HTTPException(403, 'A direct local browser administrator is required')
    require_admin(request)


PROJECT_ID = re.compile(r'[0-9a-f]{10}')


def _private(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Pragma'] = 'no-cache'
    response.headers['X-Content-Type-Options'] = 'nosniff'


class Preview(BaseModel):
    action: str = Field(max_length=10)
    path: str = Field(max_length=600)
    project_id: str = Field(default='', max_length=40)


class Apply(BaseModel):
    confirmation: str = Field(max_length=200)
    typed_name: str = Field(default='', max_length=300)


def _fail_closed_without_auth():
    from src.owner_identity import auth_disabled
    return auth_disabled()


def make_projects_dashboard_router(get_dashboard, guard=_reader, get_grants=None, write_guard=require_browser_admin, grants_enabled=grants_switch,
                                   auth_is_off=_fail_closed_without_auth):
    router = APIRouter(prefix='/api/projects-dashboard', tags=['projects-dashboard'])

    @router.get('')
    def overview(request: Request, response: Response):
        guard(request)
        _private(response)
        result = get_dashboard().overview()
        result['grants'] = {'available': get_grants is not None, 'enabled': bool(get_grants is not None and grants_enabled()), 'switch': 'ODYSSEUS_PROJECTS_GRANTS'}
        return result

    @router.get('/sessions')
    def sessions(request: Request, response: Response):
        guard(request)
        _private(response)
        return {'sessions': get_dashboard().sessions()}

    @router.get('/project/{project_id}')
    def project(project_id: str, request: Request, response: Response, related: bool = True):
        guard(request)
        _private(response)
        if not PROJECT_ID.fullmatch(project_id):
            raise HTTPException(404, 'Unknown project')
        result = get_dashboard().detail(project_id, port=request.url.port or 7000, related=related)
        if result is None:
            raise HTTPException(404, 'Unknown project')
        return result

    @router.get('/project/{project_id}/related')
    def project_related(project_id: str, request: Request, response: Response):
        guard(request)
        _private(response)
        if not PROJECT_ID.fullmatch(project_id):
            raise HTTPException(404, 'Unknown project')
        result = get_dashboard().related_detail(project_id, port=request.url.port or 7000)
        if result is None:
            raise HTTPException(404, 'Unknown project')
        return {'related_sessions': result}

    if get_grants is not None:
        def user_of(request):
            return str(getattr(request.state, 'current_user', None) or 'admin')

        def mutation_gate(request):
            # Order matters: the switch first (nothing else is evaluated or revealed when off), then the usual same-origin administrator
            # check, then an explicit refusal when authentication is disabled (require_admin would let everyone through).
            if not grants_enabled():
                raise HTTPException(403, 'Grant changes from the dashboard are switched off on this server.')
            write_guard(request)
            if auth_is_off():
                raise HTTPException(403, 'Grant changes need authentication to be enabled.')

        def refused(error):
            return HTTPException(error.status, str(error))

        @router.post('/grants/preview')
        def grants_preview(body: Preview, request: Request, response: Response):
            mutation_gate(request)
            _private(response)
            if body.project_id and not PROJECT_ID.fullmatch(body.project_id):
                raise HTTPException(404, 'Unknown project')
            try:
                return get_grants().preview(body.action, body.path, body.project_id, user_of(request))
            except GrantError as error:
                raise refused(error)

        @router.post('/grants/apply')
        def grants_apply(body: Apply, request: Request, response: Response):
            mutation_gate(request)
            _private(response)
            try:
                return get_grants().apply(body.confirmation, body.typed_name, user_of(request), remote=request.client.host if request.client else None)
            except GrantError as error:
                raise refused(error)

        @router.get('/grants/audit')
        def grants_audit(request: Request, response: Response, limit: int = 20):
            guard(request)
            _private(response)
            return {'audit': get_grants().audit(limit)}

    return router
