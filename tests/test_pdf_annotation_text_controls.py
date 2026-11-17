from src.pdf_form_doc import parse_markdown_annotations


def test_annotation_font_size_round_trips_from_markdown():
    content = (
        "- Signed text <!-- annotation id=ann-1 page=1 x=10.00 y=20.00 "
        "w=30.00 h=4.00 kind=text lh=1.35 fs=18.0 -->"
    )

    annotations = parse_markdown_annotations(content)

    assert len(annotations) == 1
    assert annotations[0]["line_height"] == 1.35
    assert annotations[0]["font_size"] == 18.0


def test_old_annotation_without_font_size_defaults_to_eleven_points():
    content = (
        "- Legacy <!-- annotation id=ann-old page=1 x=1 y=2 "
        "w=3 h=4 kind=text lh=1.30 -->"
    )

    assert parse_markdown_annotations(content)[0]["font_size"] == 11.0
