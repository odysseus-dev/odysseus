"""Deleting a chat preserves Gallery assets unless explicitly selected."""
import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from core.database import Base, Session, ChatMessage, GalleryImage
from src import session_image_cleanup


@pytest.mark.parametrize('delete_images', [False, True])
def test_chat_deletion_respects_image_choice(tmp_path, monkeypatch, delete_images):
    import core.session_manager as manager_module
    engine = create_engine(f'sqlite:///{tmp_path / "chat.db"}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(manager_module, 'SessionLocal', factory)
    monkeypatch.setattr(session_image_cleanup, 'GENERATED_IMAGES_DIR', str(tmp_path))
    image_path = tmp_path / 'picture.png'
    image_path.write_bytes(b'image fixture')
    with factory() as db:
        db.add(Session(id='chat', name='Test', endpoint_url='http://example.test', model='test', owner='alice'))
        db.add(ChatMessage(id='message', session_id='chat', role='assistant', content='An image'))
        db.add(GalleryImage(id='image', filename=image_path.name, owner='alice', session_id='chat', is_active=True))
        db.commit()
    manager = manager_module.SessionManager.__new__(manager_module.SessionManager)
    manager.sessions = {}
    if delete_images:
        assert manager.delete_session('chat', delete_images=True)
    else:
        # Omitting the option must preserve images, including automated callers.
        assert manager.delete_session('chat')
    with factory() as db:
        assert db.get(Session, 'chat') is None
        assert db.query(ChatMessage).count() == 0
        image = db.get(GalleryImage, 'image')
        assert image.is_active is (not delete_images)
        if not delete_images:
            assert image.session_id is None
    assert image_path.exists() is (not delete_images)
    engine.dispose()


def test_image_references_do_not_delete_another_chat_or_owners_gallery(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "scope.db"}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_image_cleanup, 'GENERATED_IMAGES_DIR', str(tmp_path))
    with factory() as db:
        db.add(Session(id='chat', name='Test', endpoint_url='http://example.test', model='test', owner='alice'))
        db.add(Session(id='other-chat', name='Other', endpoint_url='http://example.test', model='test', owner='alice'))
        for image_id, owner, chat in [('other-owner', 'bob', None), ('other-chat-image', 'alice', 'other-chat')]:
            db.add(GalleryImage(id=image_id, filename=image_id+'.png', owner=owner, session_id=chat, is_active=True))
        db.add(ChatMessage(id='message', session_id='chat', role='assistant', content='References', meta_data=json.dumps({'tool_events': [{'image_id': 'other-owner'}, {'image_id': 'other-chat-image'}]})))
        db.commit()
        assert session_image_cleanup.session_gallery_images(db, 'chat').count() == 0
        assert session_image_cleanup.cleanup_session_images('chat', db=db) == 0
        assert all(image.is_active for image in db.query(GalleryImage).all())
    engine.dispose()
