from dotenv import load_dotenv
load_dotenv()
import os
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from core.constants import BASE_DIR, STATIC_DIR
from core.middleware import SecurityHeadersMiddleware, RequestTimeoutMiddleware
from core.auth_middleware import setup_auth
from core.exceptions import (
    SessionNotFoundError, InvalidFileUploadError,
    LLMServiceError, WebSearchError,
)
from core.lifecycle import on_startup, on_shutdown
from routes.spa_routes import RevalidatingStatic
from routes.register import register_routes
from services.youtube import init_youtube
from src.app_initializer import initialize_managers

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

# ── App ──
app = FastAPI(
    title="AI Chat Application",
    description="Comprehensive AI chat with memory, research, and multi-modal capabilities",
    version="1.0.0",
)

# ── Middleware ──
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost,http://127.0.0.1").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestTimeoutMiddleware)

# ── Auth ──
setup_auth(app)

# ── Static files ──
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", RevalidatingStatic(directory="static"), name="static")

# ── Initialize components ──
init_youtube()

rag_manager = None
rag_available = False
logger.info("Vector document RAG disabled (unused)")

components = initialize_managers(BASE_DIR, rag_manager)

# ── Exception handlers ──
@app.exception_handler(SessionNotFoundError)
async def session_not_found_handler(request: Request, exc: SessionNotFoundError):
    return JSONResponse(status_code=404, content={"error": "SESSION_NOT_FOUND", "message": str(exc)})

@app.exception_handler(InvalidFileUploadError)
async def invalid_file_upload_handler(request: Request, exc: InvalidFileUploadError):
    return JSONResponse(status_code=400, content={"error": "INVALID_FILE_UPLOAD", "message": str(exc)})

@app.exception_handler(LLMServiceError)
async def llm_service_error_handler(request: Request, exc: LLMServiceError):
    return JSONResponse(status_code=502, content={"error": "LLM_SERVICE_ERROR", "message": str(exc)})

@app.exception_handler(WebSearchError)
async def web_search_error_handler(request: Request, exc: WebSearchError):
    return JSONResponse(status_code=502, content={"error": "WEB_SEARCH_ERROR", "message": str(exc)})

# ── Routes ──
register_routes(app, components, rag_manager, rag_available)

# ── Lifecycle ──
@app.on_event("startup")
async def startup_event():
    await on_startup(app)

@app.on_event("shutdown")
async def shutdown_event():
    await on_shutdown(app)
