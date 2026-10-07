import json
import pytest
from src.agent_tools.web_tools import YouTubeTool
from src.clean_agent_preview import youtube_reference_error


@pytest.mark.parametrize('role,content,allowed', [
    ('assistant', 'https://youtube.com/watch?v=abcdefghijk', False),
    ('tool', '{"exit_code":1,"video_url":"https://youtube.com/watch?v=abcdefghijk"}', False),
    ('tool', 'Video ID: abcdefghijk', True),
    ('tool', '- link "Video title" [ref=e78]', False),
    ('user', 'https://youtube.com/watch?v=abcdefghijk', True),
])
def test_video_identity_requires_evidence(role, content, allowed):
    error = youtube_reference_error('youtube_tool', {'action': 'comments', 'video_id': 'abcdefghijk'},
                                    user_text='Read comments on it', history=[{'role': role, 'content': content}])
    assert bool(error) is not allowed


@pytest.mark.asyncio
@pytest.mark.parametrize('target', [
    'https://www.youtube.com/watch?v=example_last_vlog',
    'https://www.youtube.com/watch?v=abcdefghijkEXTRA',
    'https://www.youtube.com/@creator/videos',
    'https://notyoutube.example/watch?v=abcdefghijk',
    'invented_title',
])
async def test_invalid_target_rejected_before_network(monkeypatch, target):
    async def unexpected(*args, **kwargs):
        pytest.fail('Invalid target must not reach retrieval')
    monkeypatch.setattr(YouTubeTool, '_comments_from_data_api', unexpected)
    result = await YouTubeTool().execute(json.dumps({'action': 'comments', 'video_url': target}), {})
    assert result['exit_code'] == 1
    assert result['failure_kind'] == 'invalid_target'
    assert 'actual video URL' in result['error']


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['abcdefghijk',
    'https://www.youtube.com/watch?v=abcdefghijk&t=42',
    'https://youtu.be/abcdefghijk', 'https://www.youtube.com/shorts/abcdefghijk'])
async def test_valid_targets_reach_retrieval(monkeypatch, target):
    seen = []
    async def metadata(self, url):
        seen.append(url)
        return {'output': 'fixture', 'exit_code': 0}
    monkeypatch.setattr(YouTubeTool, '_metadata', metadata)
    result = await YouTubeTool().execute(json.dumps({'action': 'metadata', 'video_url': target}), {})
    assert result['exit_code'] == 0 and len(seen) == 1


@pytest.mark.asyncio
async def test_comment_failure_distinguishes_retrieval_from_invalid_target(monkeypatch):
    import services.youtube.youtube_handler as handler
    async def unavailable(*args, **kwargs):
        return {'success': False, 'error': 'Fixture transport unavailable'}
    monkeypatch.setattr(YouTubeTool, '_comments_from_data_api', unavailable)
    monkeypatch.setattr(handler, 'fetch_youtube_comments', unavailable)
    result = await YouTubeTool().execute(json.dumps({
        'action': 'comments', 'video_id': 'abcdefghijk'}), {})
    assert result['failure_kind'] == 'comments_unavailable'
    assert result['video_url'] == 'https://www.youtube.com/watch?v=abcdefghijk'
    assert result['exit_code'] == 1
