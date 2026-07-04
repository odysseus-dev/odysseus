from PIL import Image
import src.document_processor as dp

class MockImage:
    def __init__(self, mode="CMYK"):
        self.image = Image.new(mode, (10, 10))

class MockPage:
    def __init__(self, images, text=""):
        self.images = images
        self._text = text
    
    def extract_text(self):
        return self._text

class MockPdfReader:
    def __init__(self, path):
        self.pages = [MockPage([MockImage("CMYK")])]

def test_process_pdf_cmyk_image(monkeypatch):
    import pypdf
    monkeypatch.setattr(pypdf, "PdfReader", MockPdfReader)
    
    # Mock analyze_image_with_vl to return custom text
    monkeypatch.setattr(dp, "analyze_image_with_vl", lambda path, owner=None: "Analyzed CMYK Image Text")
    
    # Run the PDF processing
    result = dp._process_pdf("fake_path.pdf")
    
    # Verify that the image was processed successfully without raising OSError
    assert "Analyzed CMYK Image Text" in result
