"""Gallery tag pills select complete CSV tokens while text search stays fuzzy."""
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import GalleryImage
import routes.gallery_routes as gallery


@pytest.fixture
def client(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'tags.db'}", connect_args={'check_same_thread': False}, poolclass=NullPool)
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(gallery, 'SessionLocal', factory)
    monkeypatch.setattr(gallery, 'get_current_user', lambda request: 'alice')
    rows = [
        ('manual', ' cat , portrait ', None),
        ('ai', None, ' CAT , blue '),
        ('mixed', 'cat', 'blue'),
        ('category', 'category', ''),
        ('scatter', '', 'scatter'),
        ('spaced', ' new york , travel ', ''),
        ('unspaced', 'newyork', ''),
        ('punctuation', r'100%, sale_2024, C:\photos, a.b, (cat), x+y, [tag]', ''),
        ('near', r'100abc, saleX2024, C:photos, axb, acat, xxy, t', ''),
        ('empty', None, None),
        ('foreign', 'cat,blue', ''),
        ('inactive', 'cat,blue', ''),
    ]
    with factory() as db:
        for i, (uid, tags, ai) in enumerate(rows):
            db.add(GalleryImage(id=uid, filename=uid+'.png', tags=tags, ai_tags=ai, prompt='synthetic photo',
                                owner='bob' if uid == 'foreign' else 'alice', is_active=uid != 'inactive',
                                created_at=datetime(2026, 1, 1)+timedelta(minutes=i)))
        db.commit()
        # Explicit UPDATE stores SQL NULL rather than the column's insert default.
        db.query(GalleryImage).filter(GalleryImage.id.in_(['ai', 'empty'])).update({GalleryImage.tags: None})
        db.query(GalleryImage).filter(GalleryImage.id.in_(['manual', 'empty'])).update({GalleryImage.ai_tags: None})
        db.commit()
    app = FastAPI()
    app.include_router(gallery.setup_gallery_routes())
    with TestClient(app) as instance:
        yield instance
    engine.dispose()


def library(client, **params):
    response = client.get('/api/gallery/library', params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    return {row['id'] for row in body['items']}, body


def test_complete_tokens_in_manual_and_ai_tags(client):
    ids, body = library(client, tag='cat')
    assert ids == {'manual', 'ai', 'mixed'}
    assert body['total'] == 3
    assert body['total_tagged'] == 2


@pytest.mark.parametrize('tag', ['cat, blue', ' BLUE , CAT ', 'cat,,blue,'])
def test_stacked_tags_remain_and_across_manual_and_ai(client, tag):
    assert library(client, tag=tag)[0] == {'ai', 'mixed'}


@pytest.mark.parametrize('tag,expected', [
    ('new york', {'spaced'}), ('newyork', {'unspaced'}),
    ('100%', {'punctuation'}), ('sale_2024', {'punctuation'}),
    (r'C:\photos', {'punctuation'}), ('a.b', {'punctuation'}),
    ('(cat)', {'punctuation'}), ('x+y', {'punctuation'}), ('[tag]', {'punctuation'}),
    ('%', set()), ('_', set()), ('sale', set()), ('c', set()),
])
def test_tag_text_is_literal_and_internal_spaces_are_preserved(client, tag, expected):
    assert library(client, tag=tag)[0] == expected


def test_pagination_totals_and_shuffle_follow_exact_filter(client):
    ids = set()
    for offset in range(3):
        page, body = library(client, tag='cat', sort='oldest', limit=1, offset=offset)
        assert body['total'] == 3
        assert body['total_tagged'] == 2
        assert len(page) == 1
        ids.update(page)
    assert ids == {'manual', 'ai', 'mixed'}
    assert library(client, tag='cat', sort='oldest', limit=1, offset=3)[0] == set()
    assert library(client, tag='cat', sort='shuffle', seed=7)[0] == ids


def test_free_text_search_still_matches_partial_tags(client):
    assert library(client, search='cat')[0] == {'manual', 'ai', 'mixed', 'category', 'scatter', 'punctuation', 'near'}
