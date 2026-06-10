"""
src/upload_classifier.py

Stateless file-type / content-type classification + hashing collaborator for
UploadHandler (ARCH-P6-01 / P8-T16). These functions carry no UploadHandler
instance state — they operate purely on their arguments (filename, content_type,
file object, an optional file_detector) plus mimetypes — so they live here as a
focused, single-responsibility module. UploadHandler delegates to them via thin
methods that preserve the original signatures, so every importer/test that calls
handler.is_image_file(...) etc. keeps working unchanged.
"""

import hashlib
import logging
import mimetypes
import os

logger = logging.getLogger(__name__)


def calculate_file_hash(file_obj) -> str:
    """Calculate SHA-256 hash of file content."""
    file_obj.seek(0)
    hash_sha256 = hashlib.sha256()
    for chunk in iter(lambda: file_obj.read(4096), b""):
        hash_sha256.update(chunk)
    file_obj.seek(0)
    return hash_sha256.hexdigest()


def detect_content_type(file_obj, original_filename: str, file_detector=None) -> str:
    """Detect MIME type based on file content, with extension fallback.

    `file_detector` is the optional python-magic detector held by UploadHandler;
    passing it keeps this function stateless while preserving behavior.
    """
    content_type = "application/octet-stream"
    if file_detector:
        try:
            file_obj.seek(0)
            content_type = file_detector.from_buffer(file_obj.read(1024))
            file_obj.seek(0)
        except Exception as e:
            logger.warning(f"Failed to detect content type: {e}")

    if not content_type or content_type == "application/octet-stream":
        _, ext = os.path.splitext(original_filename.lower())
        if ext:
            content_type = mimetypes.guess_type(original_filename)[0] or content_type

    return content_type


def is_image_file(filename: str, content_type: str = None) -> bool:
    """Check if a file is an image based on extension or content type."""
    image_extensions = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
    image_mime_types = {
        'image/png', 'image/jpeg', 'image/jpg', 'image/webp', 'image/gif'
    }

    # Check by extension
    _, ext = os.path.splitext(filename.lower())
    if ext in image_extensions:
        return True

    # Check by content type if provided
    if content_type and content_type in image_mime_types:
        return True

    return False


def is_document_file(filename: str, content_type: str = None) -> bool:
    """Check if a file is a document based on extension or content type."""
    document_extensions = {
        '.pdf', '.docx', '.xlsx', '.pptx', '.xls', '.epub',
        '.txt', '.py', '.js', '.html', '.htm',
        '.css', '.json', '.md', '.csv', '.log', '.xml', '.yml',
        '.yaml', '.nix', '.sql', '.sh', '.bash', '.c', '.cpp', '.h',
        '.java', '.go', '.rs', '.php', '.rb', '.ts', '.jsx', '.tsx'
    }
    document_mime_types = {
        'application/pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'application/vnd.ms-excel',
        'application/epub+zip',
        'text/plain'
    }

    # Check by extension
    _, ext = os.path.splitext(filename.lower())
    if ext in document_extensions:
        return True

    # Check by content type if provided
    if content_type and content_type in document_mime_types:
        return True

    return False


def is_audio_file(filename: str, content_type: str = None) -> bool:
    """Check if a file is an audio file based on extension or content type."""
    audio_extensions = {'.webm', '.wav', '.mp3', '.m4a', '.ogg'}
    audio_mime_types = {
        'audio/webm', 'audio/wav', 'audio/mpeg', 'audio/mp4', 'audio/ogg'
    }

    # Check by extension
    _, ext = os.path.splitext(filename.lower())
    if ext in audio_extensions:
        return True

    # Check by content type if provided
    if content_type and content_type in audio_mime_types:
        return True

    return False


def is_safe_file_type(content_type: str, filename: str) -> bool:
    """Check if file type is safe to store and serve."""
    dangerous_types = {
        'application/x-executable', 'application/x-sharedlib',
        'application/x-dll', 'application/x-msdownload',
        'application/x-sh', 'application/x-bat', 'application/x-vbs',
        'application/javascript', 'application/x-javascript'
    }

    dangerous_extensions = {
        '.exe', '.dll', '.bat', '.cmd', '.vbs',
        '.ps1', '.jsp', '.asp', '.aspx'
    }

    if content_type in dangerous_types:
        return False

    _, ext = os.path.splitext(filename.lower())
    if ext in dangerous_extensions:
        return False

    return True
