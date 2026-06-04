// core/exceptions.rs  <- core/exceptions.py
//! Chat-specific exceptions.

use std::fmt;

/// Define a message-only `class X(Exception)` whose `__init__(self, message)`
/// calls `super().__init__(message)` and stores `self.message = message`.
macro_rules! message_exception {
    ($name:ident, $doc:literal) => {
        #[doc = $doc]
        #[derive(Debug, Clone, Default)]
        pub struct $name {
            pub message: String,
        }

        impl $name {
            /// `raise X(message)`
            pub fn new(message: impl Into<String>) -> Self {
                $name {
                    message: message.into(),
                }
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                // str(exc) == the message passed to super().__init__.
                write!(f, "{}", self.message)
            }
        }

        impl std::error::Error for $name {}
    };
}

/// Raised when a session is not found.
#[derive(Debug, Clone, Default)]
pub struct SessionNotFoundError {
    pub session_id: String,
    // str(exc) is the formatted string built in __init__; cache it so Display
    // matches `super().__init__(f"Session '{session_id}' not found")`.
    message: String,
}

impl SessionNotFoundError {
    /// `raise SessionNotFoundError(session_id)`
    pub fn new(session_id: impl Into<String>) -> Self {
        let session_id = session_id.into();
        let message = format!("Session '{session_id}' not found");
        SessionNotFoundError {
            session_id,
            message,
        }
    }
}

impl fmt::Display for SessionNotFoundError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl std::error::Error for SessionNotFoundError {}

message_exception!(InvalidFileUploadError, "Raised when file upload validation fails.");

/// Raised when LLM service calls fail.
#[derive(Debug, Clone, Default)]
pub struct LLMServiceError {
    pub message: String,
    pub status_code: Option<i64>,
}

impl LLMServiceError {
    /// `raise LLMServiceError(message)` (status_code defaults to None).
    pub fn new(message: impl Into<String>) -> Self {
        LLMServiceError {
            message: message.into(),
            status_code: None,
        }
    }

    /// `raise LLMServiceError(message, status_code)`
    pub fn with_status_code(message: impl Into<String>, status_code: i64) -> Self {
        LLMServiceError {
            message: message.into(),
            status_code: Some(status_code),
        }
    }
}

impl fmt::Display for LLMServiceError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl std::error::Error for LLMServiceError {}

message_exception!(WebSearchError, "Raised when web search operations fail.");
message_exception!(ConfigurationError, "Raised when configuration is invalid.");
