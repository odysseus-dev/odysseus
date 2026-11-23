"""MIME alternatives are selected independently of part order and codec labels."""
from email import message_from_bytes
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pytest

from mcp_servers.email_server import _extract_text as mcp_text
from routes.email_helpers import _extract_html, _extract_text as route_text


def alternative(order, plain='Body text', html='<p>Body text</p>'):
    msg = MIMEMultipart('alternative')
    parts = {'plain': MIMEText(plain, 'plain', 'utf-8'), 'html': MIMEText(html, 'html', 'utf-8')}
    for kind in order:
        msg.attach(parts[kind])
    return message_from_bytes(msg.as_bytes())


@pytest.mark.parametrize('extract', [route_text, mcp_text], ids=['route', 'mcp'])
@pytest.mark.parametrize('order', [('html', 'plain'), ('plain', 'html')])
def test_plain_alternative_wins_in_either_order(extract, order):
    assert extract(alternative(order)) == 'Body text'


@pytest.mark.parametrize('extract', [route_text, mcp_text], ids=['route', 'mcp'])
def test_empty_plain_allows_html_fallback(extract):
    assert extract(alternative(('plain', 'html'), plain='', html='Hello<br>world &amp; all')) == 'Hello\nworld & all'


@pytest.mark.parametrize('extract', [route_text, mcp_text], ids=['route', 'mcp'])
def test_text_attachments_are_not_body_alternatives(extract):
    msg = MIMEMultipart('mixed')
    attachment = MIMEText('Attachment text', 'plain')
    attachment.add_header('Content-Disposition', 'attachment', filename='note.txt')
    msg.attach(attachment)
    msg.attach(MIMEText('<p>Body text</p>', 'html'))
    assert extract(msg) == 'Body text'


@pytest.mark.parametrize('extract', [route_text, mcp_text], ids=['route', 'mcp'])
def test_multiple_plain_body_parts_remain_joined(extract):
    msg = MIMEMultipart('mixed')
    msg.attach(MIMEText('Part one', 'plain'))
    msg.attach(MIMEText('Part two', 'plain'))
    assert extract(msg) == 'Part one\nPart two'


@pytest.mark.parametrize('extract', [route_text, mcp_text], ids=['route', 'mcp'])
@pytest.mark.parametrize('kind', ['plain', 'html'])
@pytest.mark.parametrize('multipart', [False, True])
def test_unknown_charset_falls_back_to_utf8(extract, kind, multipart):
    body = 'Hello café' if kind == 'plain' else '<p>Hello café</p>'
    raw = f'Content-Type: text/{kind}; charset=x-unknown\r\nContent-Transfer-Encoding: 8bit\r\n\r\n'.encode()+body.encode()
    part = message_from_bytes(raw)
    if multipart:
        msg = MIMEMultipart('mixed');msg.attach(part)
    else:
        msg = part
    assert extract(msg) == 'Hello café'


@pytest.mark.parametrize('extract', [route_text, mcp_text], ids=['route', 'mcp'])
def test_declared_latin1_is_preserved(extract):
    assert extract(MIMEText('café', 'plain', 'iso-8859-1')) == 'café'


@pytest.mark.parametrize('multipart', [False, True])
def test_raw_html_unknown_charset_is_readable(multipart):
    part = message_from_bytes(b'Content-Type: text/html; charset=x-unknown\r\n\r\n<p>Hello caf\xc3\xa9</p>')
    if multipart:
        msg=MIMEMultipart('mixed');msg.attach(part)
    else:msg=part
    assert _extract_html(msg) == '<p>Hello café</p>'


@pytest.mark.parametrize('extract', [route_text, mcp_text], ids=['route', 'mcp'])
def test_invalid_bytes_are_replaced_in_unknown_charset(extract):
    msg=message_from_bytes(b'Content-Type: text/plain; charset=x-unknown\r\n\r\nHello\xff')
    assert extract(msg) == 'Hello\ufffd'
