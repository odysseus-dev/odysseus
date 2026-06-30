# tests/test_launcher.py
import sys
import os
from unittest import mock
import pytest

from launcher import NullWriter, create_tray_image, on_open_browser, on_exit, open_browser


def test_null_writer():
    writer = NullWriter()
    # writing and flushing should not raise any exceptions
    writer.write("hello")
    writer.flush()
    assert writer.isatty() is False


def test_create_tray_image():
    try:
        from PIL import Image
        img = create_tray_image()
        assert isinstance(img, Image.Image)
        assert img.size == (64, 64)
    except ImportError:
        pytest.skip("Pillow/PIL not installed in test environment")


def test_on_open_browser():
    with mock.patch("webbrowser.open") as mock_open:
        icon_mock = mock.Mock()
        item_mock = mock.Mock()
        url = "http://127.0.0.1:7000"
        on_open_browser(icon_mock, item_mock, url)
        mock_open.assert_called_once_with(url)


def test_on_exit():
    with mock.patch("os._exit") as mock_exit:
        icon_mock = mock.Mock()
        item_mock = mock.Mock()
        on_exit(icon_mock, item_mock)
        icon_mock.stop.assert_called_once()
        mock_exit.assert_called_once_with(0)


def test_open_browser():
    with mock.patch("webbrowser.open") as mock_open, \
         mock.patch("time.sleep") as mock_sleep:

        # Test when splash_root is None
        with mock.patch("launcher.splash_root", None):
            open_browser("http://127.0.0.1:7000")
            mock_open.assert_called_once_with("http://127.0.0.1:7000")
            mock_sleep.assert_called_once_with(3.5)

    with mock.patch("webbrowser.open") as mock_open, \
         mock.patch("time.sleep") as mock_sleep:
        # Test when splash_root is present and gets destroyed
        mock_splash = mock.Mock()
        with mock.patch("launcher.splash_root", mock_splash):
            open_browser("http://127.0.0.1:7000")
            mock_splash.after.assert_called_once()
