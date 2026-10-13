"""
Tests for email performance optimizations:
- SMTP/IMAP connection pooling
- /email/performance-report endpoint
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import time

from app.main import app
from routes.email.email_helpers import _SMTP_POOL, _IMAP_POOL

client = TestClient(app)


def test_smtp_connection_pooling():
    """Test that SMTP connections are reused from the pool."""
    # Clear the pool before testing
    _SMTP_POOL.clear()
    
    # Mock smtplib.SMTP to track connections
    with patch('smtplib.SMTP') as mock_smtp:
        mock_conn = MagicMock()
        mock_smtp.return_value = mock_conn
        
        # Import the function after mocking to ensure it uses the mock
        from routes.email.email_helpers import _send_smtp_message
        
        # First call should create a new connection
        _send_smtp_message("test@example.com", "Test Subject", "Test Body", "sender@example.com")
        assert len(_SMTP_POOL) == 1
        
        # Second call should reuse the connection
        _send_smtp_message("test2@example.com", "Test Subject 2", "Test Body 2", "sender@example.com")
        assert len(_SMTP_POOL) == 1
        
        # Verify the connection was reused
        assert mock_smtp.call_count == 1


def test_imap_connection_pooling():
    """Test that IMAP connections are reused from the pool."""
    # Clear the pool before testing
    _IMAP_POOL.clear()
    
    # Mock imaplib.IMAP4 to track connections
    with patch('routes.email.email_helpers._open_imap_connection') as mock_imap:
        mock_conn = MagicMock()
        mock_conn.capability.return_value = ("OK", [b"CAPABILITY"])
        mock_imap.return_value = mock_conn
        
        # Mock config to avoid hitting real config
        with patch('routes.email.email_helpers._get_email_config') as mock_config:
            mock_config.return_value = {
                "imap_host": "imap.example.com",
                "imap_port": 993,
                "imap_user": "user@example.com",
                "imap_password": "password",
                "account_name": "Test Account"
            }
            
            # Import the function after mocking to ensure it uses the mock
            from routes.email.email_helpers import _imap_connect
            
            # First call should create a new connection
            conn1 = _imap_connect()
            assert len(_IMAP_POOL) == 1
            
            # Second call should reuse the connection
            conn2 = _imap_connect()
            assert len(_IMAP_POOL) == 1
            
            # Verify the same connection was returned
            assert conn1 is conn2


def test_email_performance_report_endpoint():
    """Test the /email/performance-report endpoint."""
    response = client.get("/email/performance-report")
    assert response.status_code == 200
    data = response.json()
    
    # Verify the response structure
    assert "smtp_pool_size" in data
    assert "imap_pool_size" in data
    assert "connection_metrics" in data
    assert "recent_errors" in data
    
    # Verify pool sizes match our actual pools
    assert data["smtp_pool_size"] == len(_SMTP_POOL)
    assert data["imap_pool_size"] == len(_IMAP_POOL)


def test_imap_connection_pool_recovery():
    """Test that IMAP connection pool recovers from bad connections."""
    # Clear the pool before testing
    _IMAP_POOL.clear()
    
    # Mock imaplib.IMAP4 to track connections
    with patch('routes.email.email_helpers._open_imap_connection') as mock_imap:
        # First mock a good connection
        good_conn = MagicMock()
        good_conn.capability.return_value = ("OK", [b"CAPABILITY"])
        
        # Then mock a bad connection that will fail capability check
        bad_conn = MagicMock()
        bad_conn.capability.side_effect = Exception("Connection failed")
        
        mock_imap.side_effect = [good_conn, bad_conn, good_conn]
        
        # Mock config to avoid hitting real config
        with patch('routes.email.email_helpers._get_email_config') as mock_config:
            mock_config.return_value = {
                "imap_host": "imap.example.com",
                "imap_port": 993,
                "imap_user": "user@example.com",
                "imap_password": "password",
                "account_name": "Test Account"
            }
            
            from routes.email.email_helpers import _imap_connect
            
            # First call should create and store a good connection
            conn1 = _imap_connect()
            assert len(_IMAP_POOL) == 1
            
            # Replace the good connection with a bad one in the pool
            pool_key = "imap.example.com:993:user@example.com"
            _IMAP_POOL[pool_key] = bad_conn
            
            # Next call should detect the bad connection and create a new one
            conn2 = _imap_connect()
            assert len(_IMAP_POOL) == 1
            
            # Verify we got a new connection (the third mock call)
            assert mock_imap.call_count == 3
            assert conn1 is not conn2


def test_smtp_connection_performance():
    """Test SMTP connection performance measurement."""
    # Clear the pool before testing
    _SMTP_POOL.clear()
    
    # Mock smtplib.SMTP to track performance
    with patch('smtplib.SMTP') as mock_smtp:
        mock_conn = MagicMock()
        mock_smtp.return_value = mock_conn
        
        # Import the function after mocking
        from routes.email.email_helpers import _send_smtp_message
        
        # Mock time to measure connection time
        start_time = time.time()
        with patch('time.time') as mock_time:
            mock_time.side_effect = [start_time, start_time + 0.5]  # 0.5s connection time
            
            _send_smtp_message("test@example.com", "Test Subject", "Test Body", "sender@example.com")
            
            # Verify the connection was added to the pool
            assert len(_SMTP_POOL) == 1