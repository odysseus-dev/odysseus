import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from core.database import Base, GalleryImage, GalleryAlbum
from routes.admin_wipe_routes import setup_admin_wipe_routes
from fastapi import Request

def test_wipe_gallery_clears_albums(monkeypatch):
    # 1. Create a clean in-memory database
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    
    # 2. Create test session factory
    TestSessionLocal = sessionmaker(bind=engine)
    
    # 3. Populate test database with an album and an image linked to it
    db = TestSessionLocal()
    album = GalleryAlbum(id="album-1", name="Trip to Rome")
    image = GalleryImage(id="img-1", filename="rome1.jpg", album_id="album-1")
    db.add(album)
    db.add(image)
    db.commit()
    
    assert db.query(GalleryImage).count() == 1
    assert db.query(GalleryAlbum).count() == 1
    db.close()
    
    # 4. Patch SessionLocal in routes/admin_wipe_routes.py to use our in-memory DB
    import routes.admin_wipe_routes
    monkeypatch.setattr(routes.admin_wipe_routes, "SessionLocal", TestSessionLocal)
    
    # Mock require_admin to bypass auth check (using standard pytest monkeypatch)
    monkeypatch.setattr(routes.admin_wipe_routes, "require_admin", lambda r: None)
    
    # Construct a real FastAPI Request object
    request = Request(scope={"type": "http"})
    
    # 5. Initialize the router and retrieve the handler
    router = setup_admin_wipe_routes(session_manager=None)
    wipe_route = next(r for r in router.routes if r.path == "/api/admin/wipe/{kind}")
    wipe_handler = wipe_route.endpoint
    
    # 6. Execute the wipe logic for gallery
    result = wipe_handler(kind="gallery", request=request)
    
    # 7. Assertions
    db = TestSessionLocal()
    assert db.query(GalleryImage).count() == 0
    # This assertion will fail before the fix because GalleryAlbum rows were not deleted
    assert db.query(GalleryAlbum).count() == 0
    
    # Check returned stats
    assert result["status"] == "deleted"
    assert result["kind"] == "gallery"
    assert result["count"] == 2  # 1 image + 1 album
    
    db.close()
