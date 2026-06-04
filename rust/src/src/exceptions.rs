// src/exceptions.rs  <- src/exceptions.py
//! Custom exceptions for the application.

use std::fmt;

/// Raised when a requested session is not found.
#[derive(Debug, Clone)]
pub struct SessionNotFoundError {
    pub session_id: String,
}

impl SessionNotFoundError {
    pub fn new(session_id: impl Into<String>) -> Self {
        SessionNotFoundError {
            session_id: session_id.into(),
        }
    }
}

impl fmt::Display for SessionNotFoundError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Session '{}' not found", self.session_id)
    }
}

impl std::error::Error for SessionNotFoundError {}

/// Raised when a file upload fails validation.
#[derive(Debug, Clone)]
pub struct InvalidFileUploadError {
    pub message: String,
    pub filename: Option<String>,
}

impl InvalidFileUploadError {
    pub fn new(message: impl Into<String>, filename: Option<String>) -> Self {
        InvalidFileUploadError {
            message: message.into(),
            filename,
        }
    }
}

impl fmt::Display for InvalidFileUploadError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl std::error::Error for InvalidFileUploadError {}

/// Raised when there is an error communicating with the LLM service.
#[derive(Debug, Clone)]
pub struct LLMServiceError {
    pub message: String,
    pub endpoint: Option<String>,
}

impl LLMServiceError {
    pub fn new(message: impl Into<String>, endpoint: Option<String>) -> Self {
        LLMServiceError {
            message: message.into(),
            endpoint,
        }
    }
}

impl fmt::Display for LLMServiceError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl std::error::Error for LLMServiceError {}

/// Raised when there is an error with web search functionality.
#[derive(Debug, Clone)]
pub struct WebSearchError {
    pub message: String,
    pub query: Option<String>,
}

impl WebSearchError {
    pub fn new(message: impl Into<String>, query: Option<String>) -> Self {
        WebSearchError {
            message: message.into(),
            query,
        }
    }
}

impl fmt::Display for WebSearchError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl std::error::Error for WebSearchError {}
