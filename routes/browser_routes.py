"""Human-only control of the exact private browser owned by a chat."""
import json
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, JSONResponse
from src.auth_helpers import effective_user, is_delegated_credential, is_direct_loopback_request
from src.owner_identity import auth_disabled, is_request_sentinel_owner
from routes.session_routes import _verify_session_owner
from src.agent_runtime.resources import ResourceIdentityError
from src import browser_control


def setup_browser_routes(session_manager):
    router = APIRouter(prefix='/api/browser', tags=['browser'])
    def guard(request, session_id, mutation=False):
        if is_delegated_credential(request): raise HTTPException(403, 'Browser control requires a signed-in person')
        owner = effective_user(request)
        if is_request_sentinel_owner(owner): raise HTTPException(403, 'Browser control requires a human session')
        if not owner and not (auth_disabled() and is_direct_loopback_request(request)):
            raise HTTPException(401, 'Sign in to control this browser')
        _verify_session_owner(request,session_id,session_manager)
        if mutation:
            origin = request.headers.get('origin')
            expected = urlsplit(str(request.url))
            actual = urlsplit(origin or '')
            if (actual.scheme,actual.netloc)!=(expected.scheme,expected.netloc) or request.headers.get('sec-fetch-site') in {'cross-site','same-site'}:
                raise HTTPException(403, 'Browser control requires a same-origin request')
            if request.headers.get('content-type','').split(';')[0] != 'application/json':
                raise HTTPException(415, 'Browser control requires JSON')
        if owner is None:
            owner = getattr(session_manager.get_session(session_id),'owner','')
        return str(owner or '').casefold()

    async def run(owner, session_id, action, args):
        try:
            return await browser_control.call(owner,session_id,action,args)
        except ResourceIdentityError as error:
            raise HTTPException(409,str(error)) from None
        except ValueError as error:
            raise HTTPException(400,str(error)) from None
        except TimeoutError:
            raise HTTPException(409,'Browser is busy. It remains paused; try taking control again.') from None
        except Exception:
            raise HTTPException(409,'Browser operation did not finish. Refresh its state before retrying.') from None

    @router.get('/{session_id}/state')
    async def state(request: Request, session_id: str):
        owner = guard(request,session_id)
        # Leases stay in headers, never URLs, access logs, or chat metadata.
        result = await run(owner,session_id,'state',{'client':request.headers.get('x-browser-client',''),
            'lease':request.headers.get('x-browser-lease','')})
        return JSONResponse(result,headers={'Cache-Control':'no-store'})

    @router.get('/{session_id}/frame/{incarnation}/{frame_id}')
    async def frame(request: Request, session_id: str, incarnation: str, frame_id: str):
        owner = guard(request,session_id)
        raw = await run(owner,session_id,'frame',{'incarnation':incarnation,'frame_id':frame_id})
        return Response(raw,media_type='image/jpeg',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @router.post('/{session_id}/{operation}')
    async def control(request: Request, session_id: str, operation: str):
        owner = guard(request,session_id,True)
        if operation not in {'take','refresh','input','release'}: raise HTTPException(404,'Unknown browser operation')
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>32768: raise HTTPException(413,'Browser input is too large')
        try: args = json.loads(raw)
        except ValueError: raise HTTPException(400,'Invalid browser input') from None
        if not isinstance(args,dict): raise HTTPException(400,'Browser input must be an object')
        return JSONResponse(await run(owner,session_id,operation,args),headers={'Cache-Control':'no-store'})
    return router
