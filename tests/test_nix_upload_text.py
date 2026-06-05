from src.document_processor import _is_text_file, _process_text_file
from src.upload_handler import UploadHandler


def test_nix_files_are_treated_as_readable_documents(tmp_path):
    handler = UploadHandler(str(tmp_path), str(tmp_path / "uploads"))

    assert handler.is_document_file("configuration.nix")
    assert _is_text_file("configuration.nix")


def test_nix_file_processing_includes_content_in_code_block(tmp_path):
    nix_file = tmp_path / "configuration.nix"
    nix_file.write_text("{ pkgs, ... }:\n{\n  services.openssh.enable = true;\n}\n", encoding="utf-8")

    rendered = _process_text_file(str(nix_file))

    assert "[Type: nix" in rendered
    assert "```nix" in rendered
    assert "services.openssh.enable = true;" in rendered
