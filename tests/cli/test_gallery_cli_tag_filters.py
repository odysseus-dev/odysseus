"""Gallery CLI tag filters match both sources and stack like the web library."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, GalleryAlbum, GalleryImage
from tests.helpers.cli_loader import load_script


@pytest.fixture
def gallery_cli(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'gallery.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add(GalleryAlbum(id='sea', name='Sea'))
        for index, (uid, tags, ai_tags, favorite, album) in enumerate([
            ('manual', 'sunset,beach', '', True, 'sea'),
            ('ai', '', 'Beach,sunset', False, 'sea'),
            ('split', 'sunset', 'beach', True, None),
            ('one', 'sunset', '', False, None),
            ('other', 'forest', 'moss', False, None),
            ('inactive', 'sunset,beach', 'sunset,beach', True, 'sea'),
        ]):
            db.add(GalleryImage(id=uid, filename=uid+'.png', tags=tags, ai_tags=ai_tags,
                                favorite=favorite, album_id=album, is_active=uid!='inactive',
                                created_at=datetime(2026, 1, 1) + timedelta(minutes=index)))
        db.commit()
    cli = load_script('odysseus-gallery')
    monkeypatch.setattr(cli, 'SessionLocal', sessions)
    output = []
    monkeypatch.setattr(cli, 'emit', lambda rows, args: output.extend(rows))

    def run(*argv):
        output.clear()
        args = cli._build_parser().parse_args(['list', *argv])
        args.func(args)
        return [row['id'] for row in output]

    yield run
    engine.dispose()


@pytest.mark.parametrize('tag,expected', [
    ('sunset', ['one', 'split', 'ai', 'manual']),
    ('beach,sunset', ['split', 'ai', 'manual']),
    ('sunset,beach', ['split', 'ai', 'manual']),
    (', sunset, , BEACH ,', ['split', 'ai', 'manual']),
    ('forest', ['other']),
    ('no-match', []),
    (' , ', ['other', 'one', 'split', 'ai', 'manual']),
    ('sun', ['one', 'split', 'ai', 'manual']),
])
def test_tag_sources_and_stacking(gallery_cli, tag, expected):
    assert gallery_cli('--tag', tag) == expected


@pytest.mark.parametrize('options,expected', [
    ([], ['other', 'one', 'split', 'ai', 'manual']),
    (['--tag', 'beach,sunset', '--favorites'], ['split', 'manual']),
    (['--tag', 'beach,sunset', '--album', 'Sea'], ['ai', 'manual']),
    (['--tag', 'beach', '--limit', '1'], ['split']),
])
def test_tag_filters_preserve_other_list_options(gallery_cli, options, expected):
    assert gallery_cli(*options) == expected
