from fastapi import FastAPI
from src.routers import health, whatsapp

app = FastAPI(
    title="Antigravity WhatsApp Bot",
    description="FastAPI service for remote Antigravity agent management via WhatsApp",
    version="1.0.0"
)

app.include_router(health.router)
app.include_router(whatsapp.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
