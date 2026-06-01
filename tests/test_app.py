"""
Basic tests for odysseus-ui application structure
"""
import pytest
import sys
import os

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestAppStructure:
    """Test that required modules and files exist"""

    def test_app_file_exists(self):
        """Test that app.py exists"""
        app_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.py")
        assert os.path.exists(app_path), "app.py should exist"

    def test_static_directory_exists(self):
        """Test that static directory exists"""
        static_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
        assert os.path.exists(static_path), "static directory should exist"

    def test_routes_directory_exists(self):
        """Test that routes directory exists"""
        routes_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "routes")
        assert os.path.exists(routes_path), "routes directory should exist"

    def test_src_directory_exists(self):
        """Test that src directory exists"""
        src_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")
        assert os.path.exists(src_path), "src directory should exist"

    def test_env_file_is_optional_and_ignored(self):
        """A fresh checkout should not require a private .env file."""
        root = os.path.dirname(os.path.dirname(__file__))
        gitignore_path = os.path.join(root, ".gitignore")
        with open(gitignore_path, encoding="utf-8") as fh:
            ignored = {line.strip() for line in fh}
        assert ".env" in ignored, ".env should stay local and ignored"

    def test_env_example_exists(self):
        """Test that .env.example exists"""
        env_example_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.example")
        assert os.path.exists(env_example_path), ".env.example file should exist"


class TestImports:
    """Test that key modules can be imported"""

    def test_constants_importable(self):
        """Test that constants module is importable"""
        from src.constants import BASE_DIR, STATIC_DIR, SESSIONS_FILE, MEMORY_FILE
        assert BASE_DIR is not None
        assert STATIC_DIR is not None

    def test_app_helpers_importable(self):
        """Test that app_helpers module is importable"""
        from src.app_helpers import abs_join
        assert callable(abs_join)

    def test_exceptions_importable(self):
        """Test that exceptions module is importable"""
        from src.exceptions import (
            SessionNotFoundError,
            InvalidFileUploadError,
            LLMServiceError,
            WebSearchError,
        )
        # These should be exception classes
        assert issubclass(SessionNotFoundError, Exception)


class TestRouteFiles:
    """Test that route files exist and have proper structure"""

    def test_auth_routes_exist(self):
        """Test auth_routes.py exists"""
        routes_path = os.path.dirname(os.path.dirname(__file__))
        auth_routes = os.path.join(routes_path, "routes", "auth_routes.py")
        assert os.path.exists(auth_routes), "auth_routes.py should exist"

    def test_chat_routes_exist(self):
        """Test chat_routes.py exists"""
        routes_path = os.path.dirname(os.path.dirname(__file__))
        chat_routes = os.path.join(routes_path, "routes", "chat_routes.py")
        assert os.path.exists(chat_routes), "chat_routes.py should exist"

    def test_memory_routes_exist(self):
        """Test memory_routes.py exists"""
        routes_path = os.path.dirname(os.path.dirname(__file__))
        mem_routes = os.path.join(routes_path, "routes", "memory_routes.py")
        assert os.path.exists(mem_routes), "memory_routes.py should exist"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
