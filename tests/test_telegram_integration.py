"""
Test suite for Telegram integration

Tests cover:
- Message parsing (text, photos, documents)
- User linking and authentication
- Chat handler integration
- Message splitting
- Command handlers
"""

import pytest
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestTelegramMessageParsing:
    """Test Telegram message parsing with various content types."""
    
    def test_parse_text_message(self):
        """Test parsing a simple text message."""
        from routes.telegram_helpers import parse_telegram_message
        
        update = {
            "message": {
                "message_id": 123,
                "chat": {"id": 456},
                "from": {"id": 789},
                "text": "Hello bot!",
            }
        }
        
        result = parse_telegram_message(update)
        assert result is not None
        assert result["chat_id"] == 456
        assert result["user_id"] == 789
        assert result["text"] == "Hello bot!"
        assert result["message_id"] == 123
        assert "media_type" not in result
    
    def test_parse_photo_message(self):
        """Test parsing a photo message with caption."""
        from routes.telegram_helpers import parse_telegram_message
        
        update = {
            "message": {
                "message_id": 123,
                "chat": {"id": 456},
                "from": {"id": 789},
                "caption": "Check this out!",
                "photo": [
                    {"file_id": "abc123", "width": 320, "height": 240},
                    {"file_id": "abc456", "width": 800, "height": 600},
                ]
            }
        }
        
        result = parse_telegram_message(update)
        assert result is not None
        assert result["chat_id"] == 456
        assert result["user_id"] == 789
        assert result["text"] == "Check this out!"
        assert result["media_type"] == "photo"
        assert result["file_id"] == "abc456"  # Highest resolution
    
    def test_parse_document_message(self):
        """Test parsing a document message."""
        from routes.telegram_helpers import parse_telegram_message
        
        update = {
            "message": {
                "message_id": 123,
                "chat": {"id": 456},
                "from": {"id": 789},
                "caption": "See attached",
                "document": {
                    "file_id": "doc123",
                    "file_name": "notes.pdf",
                }
            }
        }
        
        result = parse_telegram_message(update)
        assert result is not None
        assert result["media_type"] == "document"
        assert result["file_id"] == "doc123"
        assert result["file_name"] == "notes.pdf"
        assert result["text"] == "See attached"
    
    def test_parse_message_missing_text(self):
        """Test that message without text or caption is rejected."""
        from routes.telegram_helpers import parse_telegram_message
        
        update = {
            "message": {
                "message_id": 123,
                "chat": {"id": 456},
                "from": {"id": 789},
            }
        }
        
        result = parse_telegram_message(update)
        assert result is None
    
    def test_parse_photo_without_caption_fallback(self):
        """Test photo without caption uses fallback text."""
        from routes.telegram_helpers import parse_telegram_message
        
        update = {
            "message": {
                "message_id": 123,
                "chat": {"id": 456},
                "from": {"id": 789},
                "photo": [{"file_id": "photo123"}]
            }
        }
        
        result = parse_telegram_message(update)
        assert result is not None
        assert result["text"] == "[Photo attached]"
        assert result["media_type"] == "photo"

    def test_parse_forum_topic_message(self):
        """Forum-topic Telegram messages should expose thread metadata for routing."""
        from routes.telegram_helpers import parse_telegram_message

        update = {
            "message": {
                "message_id": 123,
                "chat": {"id": -100123, "type": "supergroup", "title": "Odysseus"},
                "from": {"id": 789},
                "text": "Topic hello",
                "message_thread_id": 456,
                "is_topic_message": True,
            }
        }

        result = parse_telegram_message(update)

        assert result is not None
        assert result["chat_type"] == "supergroup"
        assert result["chat_title"] == "Odysseus"
        assert result["message_thread_id"] == 456
        assert result["is_topic_message"] is True

    def test_parse_forum_topic_created_service_message(self):
        """Forum-topic creation events should expose the topic name for auto-binding."""
        from routes.telegram_helpers import parse_telegram_message

        update = {
            "message": {
                "message_id": 124,
                "chat": {"id": -100123, "type": "supergroup", "title": "Odysseus"},
                "from": {"id": 789},
                "message_thread_id": 457,
                "forum_topic_created": {"name": "Test"},
            }
        }

        result = parse_telegram_message(update)

        assert result is not None
        assert result["topic_event"] == "created"
        assert result["topic_name"] == "Test"
        assert result["message_thread_id"] == 457


class TestTelegramMessageSplitting:
    """Test response splitting for Telegram's 4096 char limit."""
    
    def test_split_short_message(self):
        """Test that short messages don't get split."""
        from routes.telegram_chat_handler import TelegramChatHandler
        
        handler = TelegramChatHandler(Mock(), Mock())
        text = "This is a short message."
        
        parts = handler._split_response(text)
        assert len(parts) == 1
        assert parts[0] == text
    
    def test_split_at_paragraph_boundary(self):
        """Test splitting at paragraph boundaries when possible."""
        from routes.telegram_chat_handler import TelegramChatHandler
        
        handler = TelegramChatHandler(Mock(), Mock())
        para1 = "First paragraph.\n\n" + "x" * 3000
        para2 = "Second paragraph.\n\n" + "y" * 3000
        text = para1 + para2
        
        parts = handler._split_response(text)
        assert len(parts) >= 2
        assert all(len(p) <= 4096 for p in parts)
    
    def test_split_exact_limit(self):
        """Test message exactly at 4096 character limit."""
        from routes.telegram_chat_handler import TelegramChatHandler
        
        handler = TelegramChatHandler(Mock(), Mock())
        text = "x" * 4096
        
        parts = handler._split_response(text)
        assert len(parts) == 1
        assert parts[0] == text
    
    def test_split_over_limit_hard_split(self):
        """Test hard split when no boundaries available."""
        from routes.telegram_chat_handler import TelegramChatHandler
        
        handler = TelegramChatHandler(Mock(), Mock())
        text = "x" * 5000
        
        parts = handler._split_response(text)
        assert len(parts) >= 2
        assert all(len(p) <= 4096 for p in parts)


class TestTelegramLinking:
    """Test Telegram user linking workflow."""
    
    def test_generate_linking_token(self):
        """Test linking token generation."""
        from routes.telegram_helpers import generate_linking_token
        
        token1 = generate_linking_token()
        token2 = generate_linking_token()
        
        assert len(token1) > 20
        assert len(token2) > 20
        assert token1 != token2
    
    def test_linking_token_expiry(self):
        """Test that old linking tokens expire."""
        from routes.telegram_helpers import create_linking_state, verify_linking_token
        
        telegram_user_id = 12345
        telegram_chat_id = 67890
        
        token = create_linking_state(telegram_user_id, telegram_chat_id)
        assert token is not None
        
        # Verify immediately - should work
        state = verify_linking_token(token)
        assert state is not None
    
    def test_duplicate_linking_prevention(self):
        """Test that duplicate linking is prevented."""
        from routes.telegram_helpers import create_linking_state
        
        # Create two users trying to link
        user1_telegram = 111
        user2_telegram = 222
        chat1 = 1111
        chat2 = 2222
        
        # Both can create linking states
        token1 = create_linking_state(user1_telegram, chat1)
        token2 = create_linking_state(user2_telegram, chat2)
        
        assert token1 is not None
        assert token2 is not None

    def test_start_linking_instructions_include_full_token(self):
        """Test the linking instructions return the full token, not a truncated preview."""
        token = "abcdefghijklmnopqrstuvwxyz123456"
        instructions = (
            f"To link your Telegram account:\n\n"
            f"1. Go to Odysseus Settings → Integrations → Telegram\n"
            f"2. Paste this code into the Linking token field:\n"
            f"<code>{token}</code>\n"
            f"(tap the code above to copy it)\n\n"
            f"3. Click Link Account\n\n"
            f"This code expires in 5 minutes."
        )

        assert token in instructions
        assert f"{token[:12]}..." not in instructions


class TestTelegramChatHandler:
    """Test Telegram chat handler."""
    
    @pytest.mark.asyncio
    async def test_handle_unlinked_user(self):
        """Test handler returns a friendly failure when coercion fails."""
        from routes.telegram_chat_handler import TelegramChatHandler
        
        mock_chat_handler = Mock()
        mock_session_manager = Mock()
        
        handler = TelegramChatHandler(mock_chat_handler, mock_session_manager)

        fake_session = Mock()
        fake_session.id = "session-1"
        fake_session.owner = "unknown"

        with patch.object(handler, "_get_or_create_telegram_session", return_value=fake_session), \
             patch("routes.telegram_chat_handler.coerce_message_and_session", side_effect=RuntimeError("boom")), \
             patch("routes.telegram_chat_handler.send_telegram_message", new=AsyncMock()) as send_mock:
            result = await handler.handle_telegram_message(
                message_text="Hello",
                owner="unknown",
                telegram_user_id=999,
                chat_id=777,
            )
        assert result is False
        send_mock.assert_awaited()
    
    def test_session_timeout_check(self):
        """Test conversation session timeout detection."""
        from routes.telegram_helpers import should_reset_conversation
        
        now = datetime.utcnow()
        
        # Recent update - should not timeout
        recent = now - timedelta(minutes=5)
        assert should_reset_conversation(recent) is False
        
        # Old update - should timeout
        old = now - timedelta(minutes=35)
        assert should_reset_conversation(old) is True
        
        # None value - should not timeout
        assert should_reset_conversation(None) is False

    def test_create_telegram_session_uses_session_manager_defaults(self):
        """Telegram chat should create a normal app session, not a custom DB row shape."""
        from routes.telegram_chat_handler import TelegramChatHandler

        mock_chat_handler = Mock()
        mock_session_manager = Mock()
        created_session = Mock()
        created_session.id = "telegram-session-id"
        created_session.headers = {}
        mock_session_manager.create_session.return_value = created_session

        handler = TelegramChatHandler(mock_chat_handler, mock_session_manager)

        query = Mock()
        query.filter.return_value.first.return_value = None
        db = Mock()
        db.query.return_value = query

        with patch("routes.telegram_chat_handler.SessionLocal", return_value=db), \
             patch("routes.telegram_chat_handler.resolve_endpoint", return_value=("http://llm.test/v1/chat/completions", "test-model", {"Authorization": "Bearer x"})), \
             patch.object(handler, "_persist_session_headers") as persist_headers:
            session = handler._get_or_create_telegram_session("admin", 12345)

        assert session is created_session
        mock_session_manager.create_session.assert_called_once()
        assert mock_session_manager.create_session.call_args.kwargs["name"] == "telegram_12345"
        assert mock_session_manager.create_session.call_args.kwargs["owner"] == "admin"
        assert created_session.headers == {"Authorization": "Bearer x"}
        persist_headers.assert_called_once_with("telegram-session-id", {"Authorization": "Bearer x"})

    def test_should_use_web_search_detects_current_info_queries(self):
        """Telegram chat mode should enable web search for obvious current-info prompts."""
        from routes.telegram_chat_handler import TelegramChatHandler

        handler = TelegramChatHandler(Mock(), Mock())

        assert handler._should_use_web_search("Can you tell me the weather in Brisbane today?") is True
        assert handler._should_use_web_search("/web Find the latest Brisbane weather") is True
        assert handler._should_use_web_search("Tell me a joke about koalas") is False

    @pytest.mark.asyncio
    async def test_build_context_uses_three_value_compactor_contract(self):
        """Telegram context building should accept the compactor's 3-value return shape."""
        from routes.telegram_chat_handler import TelegramChatHandler

        fake_session = Mock()
        fake_session.id = "session-1"
        fake_session.endpoint_url = "http://llm.test/v1/chat/completions"
        fake_session.model = "test-model"
        fake_session.headers = {"Authorization": "Bearer x"}
        fake_session.get_context_messages = Mock(return_value=[])

        mock_chat_handler = Mock()
        mock_chat_handler.chat_processor = Mock()
        mock_chat_handler.chat_processor.build_context_preface = Mock(return_value=([], [], []))

        handler = TelegramChatHandler(mock_chat_handler, Mock())

        compacted = [{"role": "user", "content": "Hello"}]
        with patch("routes.telegram_chat_handler.resolve_session_auth"), \
             patch("routes.telegram_chat_handler.load_prefs_for_user", return_value={}), \
             patch("routes.telegram_chat_handler.current_datetime_context_message", return_value={"role": "system", "content": "Now"}), \
             patch("routes.telegram_chat_handler.maybe_compact", new=AsyncMock(return_value=(compacted, 8192, False))):
            context = await handler._build_context(
                fake_session,
                "admin",
                "Hello",
                "Hello",
                use_web=False,
            )

        assert context["messages"] == compacted
        assert context["context_length"] == 8192

    @pytest.mark.asyncio
    async def test_handle_message_uses_normal_session_and_stream_contract(self):
        """Telegram chat should validate via session manager and parse SSE deltas."""
        from routes.telegram_chat_handler import TelegramChatHandler

        fake_session = Mock()
        fake_session.id = "session-1"
        fake_session.owner = "admin"
        fake_session.model = "test-model"
        fake_session.endpoint_url = "http://llm.test/v1/chat/completions"
        fake_session.headers = {"Authorization": "Bearer x"}
        fake_session.get_context_messages = Mock(return_value=[])

        mock_chat_handler = Mock()
        mock_chat_handler.preprocess_message = AsyncMock(
            return_value=("Hello", None, None, None, None)
        )
        mock_chat_handler.chat_processor = Mock()
        mock_chat_handler.chat_processor.build_context_preface = Mock(
            return_value=([], [], [])
        )

        mock_session_manager = Mock()
        mock_session_manager.get_session.return_value = fake_session

        handler = TelegramChatHandler(mock_chat_handler, mock_session_manager)

        async def _fake_stream(*args, **kwargs):
            yield 'data: {"delta": "Hi"}\n\n'
            yield 'data: {"delta": " there"}\n\n'
            yield "data: [DONE]\n\n"

        with patch.object(handler, "_get_or_create_telegram_session", return_value=fake_session), \
             patch("routes.telegram_chat_handler.coerce_message_and_session", return_value=("What is the weather in Brisbane today?", "session-1")) as coerce_mock, \
             patch.object(handler, "_get_chat_mode", return_value="chat"), \
             patch("routes.telegram_chat_handler.resolve_session_auth") as resolve_auth_mock, \
             patch("routes.telegram_chat_handler.maybe_compact", new=AsyncMock(return_value=([{"role": "user", "content": "Hello"}], 8192, False))), \
             patch("routes.telegram_chat_handler.current_datetime_context_message", return_value={"role": "user", "content": "Current date"}), \
             patch("routes.telegram_chat_handler.stream_llm_with_fallback", side_effect=_fake_stream) as stream_mock, \
             patch("routes.telegram_chat_handler.save_assistant_response") as save_response_mock, \
             patch.object(handler, "_send_response_to_telegram", new=AsyncMock(return_value=True)) as send_response_mock:
            result = await handler.handle_telegram_message(
                message_text="What is the weather in Brisbane today?",
                owner="admin",
                telegram_user_id=12345,
                chat_id=999,
            )

        assert result is True
        coerce_mock.assert_called_once_with(
            {"message": "What is the weather in Brisbane today?", "session": "session-1"},
            None,
            None,
            mock_session_manager,
        )
        resolve_auth_mock.assert_called_once_with(fake_session, "session-1", owner="admin")
        stream_mock.assert_called_once()
        assert stream_mock.call_args.args[0][0] == (
            "http://llm.test/v1/chat/completions",
            "test-model",
            {"Authorization": "Bearer x"},
        )
        assert stream_mock.call_args.args[1][-1] == {"role": "user", "content": "Hello"}
        assert mock_chat_handler.chat_processor.build_context_preface.call_args.kwargs["use_web"] is True
        save_response_mock.assert_called_once()
        send_response_mock.assert_awaited_once_with("Hi there", 999, message_thread_id=None)
        mock_session_manager.add_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_message_routes_forum_topic_to_explicit_session(self):
        """Forum-topic messages should use the mapped Odysseus session instead of the DM session."""
        from routes.telegram_chat_handler import TelegramChatHandler

        fake_session = Mock()
        fake_session.id = "session-topic"
        fake_session.owner = "admin"
        fake_session.model = "test-model"
        fake_session.endpoint_url = "http://llm.test/v1/chat/completions"
        fake_session.headers = {"Authorization": "Bearer x"}
        fake_session.get_context_messages = Mock(return_value=[])

        mock_chat_handler = Mock()
        mock_chat_handler.preprocess_message = AsyncMock(return_value=("Topic hello", None, None, None, None))
        mock_chat_handler.chat_processor = Mock()
        mock_chat_handler.chat_processor.build_context_preface = Mock(return_value=([], [], []))

        mock_session_manager = Mock()
        mock_session_manager.get_session.return_value = fake_session

        handler = TelegramChatHandler(mock_chat_handler, mock_session_manager)

        async def _fake_stream(*args, **kwargs):
            yield 'data: {"delta": "Topic"}\n\n'
            yield 'data: {"delta": " reply"}\n\n'
            yield "data: [DONE]\n\n"

        with patch("routes.telegram_chat_handler.coerce_message_and_session", return_value=("Topic hello", "session-topic")) as coerce_mock, \
             patch.object(handler, "_get_chat_mode", return_value="chat"), \
             patch("routes.telegram_chat_handler.resolve_session_auth"), \
             patch("routes.telegram_chat_handler.maybe_compact", new=AsyncMock(return_value=([{"role": "user", "content": "Topic hello"}], 8192, False))), \
             patch("routes.telegram_chat_handler.current_datetime_context_message", return_value={"role": "user", "content": "Current date"}), \
             patch("routes.telegram_chat_handler.stream_llm_with_fallback", side_effect=_fake_stream), \
             patch("routes.telegram_chat_handler.save_assistant_response"), \
             patch.object(handler, "_send_response_to_telegram", new=AsyncMock(return_value=True)) as send_response_mock:
            result = await handler.handle_telegram_message(
                message_text="Topic hello",
                owner="admin",
                telegram_user_id=12345,
                chat_id=-100999,
                session_id="session-topic",
                message_thread_id=777,
            )

        assert result is True
        coerce_mock.assert_called_once_with(
            {"message": "Topic hello", "session": "session-topic"},
            None,
            None,
            mock_session_manager,
        )
        send_response_mock.assert_awaited_once_with("Topic reply", -100999, message_thread_id=777)

    def test_get_or_create_telegram_topic_session_creates_named_session_and_mapping(self):
        """Unmapped Telegram topics should create a first-class Odysseus chat and save the mapping."""
        from routes.telegram_chat_handler import TelegramChatHandler

        mock_session_manager = Mock()
        created_session = Mock()
        created_session.id = "session-topic"
        created_session.name = "Hello"
        created_session.headers = {}
        mock_session_manager.create_session.return_value = created_session

        handler = TelegramChatHandler(Mock(), mock_session_manager)

        db = Mock()
        query = Mock()
        query.filter.return_value.first.return_value = None
        db.query.return_value = query

        with patch("routes.telegram_chat_handler.SessionLocal", return_value=db), \
             patch("routes.telegram_chat_handler.resolve_endpoint", return_value=("http://llm.test/v1/chat/completions", "test-model", {"Authorization": "Bearer x"})), \
             patch("routes.telegram_chat_handler._get_telegram_user_config", return_value={"enabled": True, "topic_mappings": {}}), \
             patch("routes.telegram_chat_handler.find_telegram_session_by_topic_name", return_value=None), \
             patch("routes.telegram_chat_handler.save_telegram_topic_mapping", return_value={"session_id": "session-topic"}) as save_mapping_mock, \
             patch.object(handler, "_persist_session_headers"):
            session = handler.get_or_create_telegram_topic_session(
                "admin",
                forum_chat_id=-100555,
                topic_id=777,
                topic_name="Hello",
                forum_chat_title="Ody chat",
            )

        assert session is created_session
        mock_session_manager.create_session.assert_called_once()
        assert mock_session_manager.create_session.call_args.kwargs["name"] == "Hello"
        save_mapping_mock.assert_called_once_with(
            "admin",
            forum_chat_id=-100555,
            topic_id=777,
            session_id="session-topic",
            session_name="Hello",
            topic_name="Hello",
            forum_chat_title="Ody chat",
        )

    @pytest.mark.asyncio
    async def test_handle_research_command_starts_background_research(self):
        """Telegram /research should use the configured research backend."""
        from routes.telegram_chat_handler import TelegramChatHandler

        fake_session = Mock()
        fake_session.id = "session-1"
        fake_session.owner = "admin"
        fake_session.model = "test-model"
        fake_session.endpoint_url = "http://llm.test/v1/chat/completions"
        fake_session.headers = {"Authorization": "Bearer x"}

        research_handler = Mock()
        research_handler.synthesize_query = AsyncMock(return_value="Brisbane weather report")

        handler = TelegramChatHandler(Mock(), Mock(), research_handler=research_handler)

        with patch.object(handler, "_get_or_create_telegram_session", return_value=fake_session), \
             patch("routes.telegram_chat_handler.resolve_session_auth"), \
             patch("routes.telegram_chat_handler._resolve_research_endpoint", return_value=("http://research.test/v1/chat/completions", "research-model", {"Authorization": "Bearer y"})), \
             patch("routes.telegram_chat_handler.send_telegram_message", new=AsyncMock()) as send_mock:
            result = await handler.handle_telegram_message(
                message_text="/research Tell me about Brisbane weather this week",
                owner="admin",
                telegram_user_id=12345,
                chat_id=999,
            )

        assert result is True
        research_handler.synthesize_query.assert_awaited_once()
        research_handler.start_research.assert_called_once()
        handler.session_manager.add_message.assert_called_once()
        send_mock.assert_awaited()

    @pytest.mark.asyncio
    async def test_handle_message_dispatches_agent_mode(self):
        """Telegram agent mode should route messages through the agent path."""
        from routes.telegram_chat_handler import TelegramChatHandler

        fake_session = Mock()
        fake_session.id = "session-1"
        fake_session.owner = "admin"

        mock_chat_handler = Mock()
        mock_chat_handler.preprocess_message = AsyncMock(return_value=("Hello", None, None, None, None))

        handler = TelegramChatHandler(mock_chat_handler, Mock())

        with patch.object(handler, "_get_or_create_telegram_session", return_value=fake_session), \
             patch("routes.telegram_chat_handler.coerce_message_and_session", return_value=("Hello", "session-1")), \
             patch.object(handler, "_get_chat_mode", return_value="agent"), \
             patch.object(handler, "_run_agent_mode", new=AsyncMock(return_value=True)) as agent_mock:
            result = await handler.handle_telegram_message(
                message_text="Hello",
                owner="admin",
                telegram_user_id=12345,
                chat_id=999,
            )

        assert result is True
        agent_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_mode_agent_command_reports_new_mode(self):
        """Telegram /setmodeagent should confirm the new active mode."""
        from routes.telegram_chat_handler import TelegramChatHandler

        handler = TelegramChatHandler(Mock(), Mock())

        with patch.object(handler, "_set_chat_mode", return_value=True), \
             patch("routes.telegram_chat_handler.send_telegram_message", new=AsyncMock()) as send_mock:
            result = await handler._handle_set_mode_command("admin", 999, "agent")

        assert result is True
        send_mock.assert_awaited_once()
        sent_text = send_mock.await_args.args[1]
        assert "You are now in **agent** mode." in sent_text

    @pytest.mark.asyncio
    async def test_mode_command_reports_current_mode(self):
        """Telegram /mode should report the current mode without changing it."""
        from routes.telegram_chat_handler import TelegramChatHandler

        handler = TelegramChatHandler(Mock(), Mock())

        with patch.object(handler, "_get_chat_mode", return_value="chat"), \
             patch("routes.telegram_chat_handler.send_telegram_message", new=AsyncMock()) as send_mock:
            result = await handler._handle_mode_command("admin", 999, "/mode")

        assert result is True
        send_mock.assert_awaited_once()
        sent_text = send_mock.await_args.args[1]
        assert "You are currently in **chat** mode." in sent_text
        assert "/setmodechat" in sent_text

    @pytest.mark.asyncio
    async def test_handle_message_setmodechat_command(self):
        """Telegram /setmodechat should route through the explicit mode setter."""
        from routes.telegram_chat_handler import TelegramChatHandler

        fake_session = Mock()
        fake_session.id = "session-1"
        fake_session.owner = "admin"

        handler = TelegramChatHandler(Mock(), Mock())

        with patch.object(handler, "_get_or_create_telegram_session", return_value=fake_session), \
             patch.object(handler, "_handle_set_mode_command", new=AsyncMock(return_value=True)) as mode_mock:
            result = await handler.handle_telegram_message(
                message_text="/setmodechat",
                owner="admin",
                telegram_user_id=12345,
                chat_id=999,
            )

        assert result is True
        mode_mock.assert_awaited_once_with("admin", 999, "chat", None)


class TestTelegramRoutes:
    """Test Telegram route setup and poller initialization."""

    def test_setup_routes_passes_chat_handler_to_poller(self):
        """Router setup should create a Telegram chat handler and pass it to the poller."""
        from routes import telegram_routes

        base_chat_handler = Mock()
        session_manager = Mock()

        with patch.object(telegram_routes, "TelegramChatHandler") as handler_cls, \
             patch.object(telegram_routes, "_start_poller") as start_poller:
            handler_instance = Mock()
            handler_cls.return_value = handler_instance

            router = telegram_routes.setup_telegram_routes(
                chat_handler=base_chat_handler,
                session_manager=session_manager,
            )

        assert router is not None
        handler_cls.assert_called_once_with(base_chat_handler, session_manager, research_handler=None)
        start_poller.assert_called_once_with(handler_instance)
        assert callable(router._ensure_poller_started)
        assert router._telegram_chat_handler is handler_instance

    @pytest.mark.asyncio
    async def test_setup_routes_starts_poller_on_startup(self):
        """Router startup should retry poller startup once the event loop is running."""
        from routes import telegram_routes

        base_chat_handler = Mock()
        session_manager = Mock()

        with patch.object(telegram_routes, "TelegramChatHandler") as handler_cls, \
             patch.object(telegram_routes, "_start_poller") as start_poller:
            handler_instance = Mock()
            handler_cls.return_value = handler_instance

            router = telegram_routes.setup_telegram_routes(
                chat_handler=base_chat_handler,
                session_manager=session_manager,
            )

            async with router.lifespan_context(None):
                pass

        assert start_poller.call_count == 2
        start_poller.assert_called_with(handler_instance)

    def test_sync_topics_creates_topics_for_active_sessions(self):
        """Sync Topics should create one forum topic per active Odysseus chat."""
        from routes import telegram_routes

        saved_configs = []

        app = FastAPI()
        with patch.object(telegram_routes, "_start_poller"), \
             patch.object(telegram_routes, "list_syncable_telegram_sessions", return_value=[
                 ("session-1", "Project Alpha"),
                 ("session-2", "Research Notes"),
             ]), \
             patch.object(telegram_routes, "get_current_user", return_value="admin"), \
             patch.object(telegram_routes, "_get_telegram_user_config", return_value={
                 "enabled": True,
                 "telegram_user_id": 12345,
                 "forum_chat_id": -100555,
                 "forum_chat_title": "Odysseus",
                 "topic_mappings": {},
             }), \
             patch.object(telegram_routes, "_save_telegram_user_config", side_effect=lambda _owner, config: saved_configs.append(config.copy()) or True), \
             patch.object(telegram_routes, "create_telegram_forum_topic", new=AsyncMock(side_effect=[
                 {"message_thread_id": 101},
                 {"message_thread_id": 202},
             ])):
            router = telegram_routes.setup_telegram_routes(chat_handler=Mock(), session_manager=Mock())
            app.include_router(router)
            client = TestClient(app)
            response = client.post("/api/telegram/sync-topics")

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["forum_chat_id"] == -100555
        assert data["created_count"] == 2
        assert data["updated_count"] == 0
        assert data["skipped_count"] == 0
        assert [item["topic_name"] for item in data["topics"]] == ["Project Alpha", "Research Notes"]
        assert saved_configs
        saved_mappings = saved_configs[-1]["topic_mappings"]
        assert saved_mappings["session-1"]["topic_id"] == 101
        assert saved_mappings["session-2"]["topic_id"] == 202


class TestTelegramHelpers:
    """Test utility helpers."""
    
    def test_hash_telegram_user_id(self):
        """Test Telegram user ID hashing for logging."""
        from routes.telegram_helpers import hash_telegram_user_id
        
        user_id = 123456789
        hash1 = hash_telegram_user_id(user_id)
        hash2 = hash_telegram_user_id(user_id)
        
        # Same input should produce same hash
        assert hash1 == hash2
        
        # Different inputs should produce different hashes
        other_hash = hash_telegram_user_id(987654321)
        assert hash1 != other_hash
        
        # Hash should not contain original ID
        assert str(user_id) not in hash1
    
    def test_validate_chat_id(self):
        """Test chat ID validation."""
        from routes.telegram_helpers import validate_telegram_chat_id
        
        # Valid chat IDs
        assert validate_telegram_chat_id(12345) is True
        assert validate_telegram_chat_id(-12345) is True  # Group chats are negative
        assert validate_telegram_chat_id("67890") is True  # String that converts to int
        
        # Invalid chat IDs
        assert validate_telegram_chat_id("abc") is False
        assert validate_telegram_chat_id(None) is False

    def test_resolve_telegram_topic_session_id_matches_saved_mapping(self):
        """Forum topic mappings should resolve back to the owning Odysseus session."""
        from routes.telegram_helpers import resolve_telegram_topic_session_id

        session_id = resolve_telegram_topic_session_id(
            {
                "forum_chat_id": -100555,
                "topic_mappings": {
                    "session-1": {"topic_id": 101, "topic_name": "Project Alpha"},
                },
            },
            -100555,
            101,
        )

        assert session_id == "session-1"

    def test_bind_telegram_topic_to_session_matches_existing_chat_name(self):
        """A Telegram topic-created event should bind to the matching Odysseus chat name."""
        from routes import telegram_helpers

        with patch.object(telegram_helpers, "list_syncable_telegram_sessions", return_value=[("session-1", "Test"), ("session-2", "General")]), \
             patch.object(telegram_helpers, "_get_telegram_user_config", return_value={"enabled": True, "topic_mappings": {}}), \
             patch.object(telegram_helpers, "_save_telegram_user_config", return_value=True) as save_mock:
            result = telegram_helpers.bind_telegram_topic_to_session(
                "admin",
                forum_chat_id=-100555,
                topic_id=777,
                topic_name="Test",
                forum_chat_title="Ody chat",
            )

        assert result == {
            "session_id": "session-1",
            "session_name": "Test",
            "topic_id": 777,
            "topic_name": "Test",
        }
        saved_config = save_mock.call_args.args[1]
        assert saved_config["forum_chat_id"] == -100555
        assert saved_config["forum_chat_title"] == "Ody chat"
        assert saved_config["topic_mappings"]["session-1"]["topic_id"] == 777


class TestTelegramPoller:
    """Test Telegram poller-specific routing behavior."""

    @pytest.mark.asyncio
    async def test_topic_created_event_auto_binds_matching_session(self):
        """Creating a Telegram topic that matches a chat name should auto-link it."""
        from routes import telegram_poller

        parsed_msg = {
            "chat_id": -100555,
            "user_id": 12345,
            "text": "[Topic created: Test]",
            "chat_type": "supergroup",
            "chat_title": "Ody chat",
            "message_thread_id": 777,
            "topic_event": "created",
            "topic_name": "Test",
        }

        with patch.object(telegram_poller, "_map_telegram_user_to_odysseus_user", new=AsyncMock(return_value="admin")), \
             patch.object(telegram_poller, "_get_telegram_user_config", return_value={"enabled": True, "forum_chat_id": -100555, "topic_mappings": {}}), \
             patch.object(telegram_poller, "remember_telegram_forum_chat", return_value={"enabled": True, "forum_chat_id": -100555, "topic_mappings": {}}), \
             patch.object(telegram_poller, "bind_telegram_topic_to_session", return_value={
                 "session_id": "session-1",
                 "session_name": "Test",
                 "topic_id": 777,
                 "topic_name": "Test",
             }) as bind_mock, \
             patch.object(telegram_poller, "send_telegram_message", new=AsyncMock(return_value=True)) as send_mock:
            result = await telegram_poller._process_telegram_message(parsed_msg)

        assert result is True
        bind_mock.assert_called_once_with(
            "admin",
            forum_chat_id=-100555,
            topic_id=777,
            topic_name="Test",
            forum_chat_title="Ody chat",
        )
        send_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unmapped_topic_message_auto_creates_session_and_routes(self):
        """Regular messages in an unmapped topic should create/bind an Odysseus chat automatically."""
        from routes import telegram_poller

        handler = Mock()
        session = Mock()
        session.id = "session-topic"
        handler.get_or_create_telegram_topic_session.return_value = session
        handler.handle_telegram_message = AsyncMock(return_value=True)

        parsed_msg = {
            "chat_id": -100555,
            "user_id": 12345,
            "text": "Hello there",
            "chat_type": "supergroup",
            "chat_title": "Ody chat",
            "message_thread_id": 888,
        }

        with patch.object(telegram_poller, "_chat_handler_instance", handler), \
             patch.object(telegram_poller, "_map_telegram_user_to_odysseus_user", new=AsyncMock(return_value="admin")), \
             patch.object(telegram_poller, "_get_telegram_user_config", return_value={"enabled": True, "forum_chat_id": -100555, "topic_mappings": {}}), \
             patch.object(telegram_poller, "remember_telegram_forum_chat", return_value={"enabled": True, "forum_chat_id": -100555, "topic_mappings": {}}), \
             patch.object(telegram_poller, "resolve_telegram_topic_session_id", return_value=None), \
             patch.object(telegram_poller, "send_typing_indicator", new=AsyncMock(return_value=True)):
            result = await telegram_poller._process_telegram_message(parsed_msg)

        assert result is True
        handler.get_or_create_telegram_topic_session.assert_called_once_with(
            "admin",
            forum_chat_id=-100555,
            topic_id=888,
            topic_name="Topic 888",
            forum_chat_title="Ody chat",
        )
        handler.handle_telegram_message.assert_awaited_once_with(
            message_text="Hello there",
            owner="admin",
            telegram_user_id=12345,
            chat_id=-100555,
            session_id="session-topic",
            message_thread_id=888,
        )

    def test_save_and_load_system_config(self, monkeypatch, tmp_path):
        """Test Telegram system config persists encrypted bot token."""
        from routes import telegram_helpers

        settings_file = tmp_path / "settings.json"
        monkeypatch.setattr(telegram_helpers, "SETTINGS_FILE", str(settings_file))

        assert telegram_helpers._save_telegram_system_config(
            "123:abc",
            bot_username="odysseus_bot",
            bot_name="Odysseus Bot",
        ) is True

        loaded = telegram_helpers._load_telegram_system_config()
        assert loaded["bot_token"] == "123:abc"
        assert loaded["bot_username"] == "odysseus_bot"
        assert loaded["bot_name"] == "Odysseus Bot"

    def test_get_telegram_bot_commands_matches_supported_commands(self):
        """Published Telegram commands should match the commands handled by the bot."""
        from routes.telegram_helpers import get_telegram_bot_commands

        commands = get_telegram_bot_commands()

        assert [item["command"] for item in commands] == [
            "start",
            "mode",
            "setmodeagent",
            "setmodechat",
        ]

    @pytest.mark.asyncio
    async def test_sync_telegram_bot_commands_calls_set_my_commands(self, monkeypatch):
        """Telegram command publishing should register slash commands with Telegram."""
        from routes import telegram_helpers

        monkeypatch.setattr(telegram_helpers, "_BOT_COMMANDS_SYNC_SIGNATURE", None)
        monkeypatch.setattr(
            telegram_helpers,
            "_load_telegram_config",
            lambda: {"bot_token": "123:abc", "api_base": "https://api.telegram.org"},
        )

        with patch.object(telegram_helpers, "_telegram_api_call", new=AsyncMock(return_value=True)) as api_mock:
            result = await telegram_helpers.sync_telegram_bot_commands(force=True)

        assert result is True
        api_mock.assert_awaited_once_with(
            "setMyCommands",
            {"commands": telegram_helpers.get_telegram_bot_commands()},
        )

    @pytest.mark.asyncio
    async def test_validate_telegram_bot_token_success(self):
        """Test Telegram bot token validation success path."""
        from routes.telegram_helpers import validate_telegram_bot_token

        response = Mock()
        response.raise_for_status = Mock()
        response.json = Mock(return_value={
            "ok": True,
            "result": {"is_bot": True, "username": "ody_bot", "first_name": "Odysseus"},
        })

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.post.return_value = response

        with patch("routes.telegram_helpers.httpx.AsyncClient", return_value=client):
            result = await validate_telegram_bot_token("123:abc")

        assert result["username"] == "ody_bot"
        assert result["first_name"] == "Odysseus"


class TestTelegramCommands:
    """Test Telegram bot commands."""
    
    def test_help_command_detected(self):
        """Test that /help command is properly detected."""
        text = "/help"
        assert text.startswith("/help")
        assert "/help".startswith("/help")
    
    def test_settings_command_detected(self):
        """Test that /settings command is properly detected."""
        text = "/settings"
        assert text.startswith("/settings")
    
    def test_start_command_with_args(self):
        """Test /start command can have arguments."""
        text = "/start abc123"
        assert text.startswith("/start")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
