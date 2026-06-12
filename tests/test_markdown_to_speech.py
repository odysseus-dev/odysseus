"""markdown_to_speech turns assistant markdown into speakable prose: TTS must
not read "double star" for **bold**, URLs, LaTeX, or code dumps aloud.
Mirrors static/js/ttsText.js — behavior changes belong in both."""
from services.tts.markdown_to_speech import markdown_to_speech


def test_empty_and_none():
    assert markdown_to_speech("") == ""
    assert markdown_to_speech(None) == ""


def test_bold_italic_strikethrough_markers_removed():
    assert markdown_to_speech("**bold** and *italic* and ~~gone~~ and __under__") == \
        "bold and italic and gone and under"


def test_links_keep_label_only():
    assert markdown_to_speech("See [the docs](https://example.com/x) here.") == "See the docs here."


def test_bare_urls_removed():
    out = markdown_to_speech("Visit https://example.com/page for more.")
    assert "https" not in out and "example.com" not in out


def test_fenced_code_blocks_removed():
    src = "Before.\n\n```python\nprint('hi')\n```\n\nAfter."
    out = markdown_to_speech(src)
    assert "print" not in out
    assert "Before." in out and "After." in out


def test_unclosed_fence_dropped():
    out = markdown_to_speech("Text.\n```js\nlet x = 1;")
    assert "let x" not in out
    assert "Text." in out


def test_inline_code_keeps_content():
    assert markdown_to_speech("Run `npm install` first.") == "Run npm install first."


def test_thinking_blocks_removed():
    src = "<think>secret reasoning</think>The answer is 4."
    assert markdown_to_speech(src) == "The answer is 4."
    # Unclosed thinking block after real reply text (mid-stream cutoff) is dropped
    assert markdown_to_speech("Visible. <thinking>partial") == "Visible."


def test_thinking_tag_with_attributes_removed():
    # e.g. <think time="12.3"> — the attribute must not defeat the strip
    src = '<think time="12.3">deep reasoning here</think>Answer.'
    assert markdown_to_speech(src) == "Answer."


def test_thought_tag_removed():
    src = "<thought>internal monologue</thought>The result is 7."
    assert markdown_to_speech(src) == "The result is 7."


def test_orphan_closer_drops_leaked_reasoning():
    # Some models emit reasoning with no opening tag, closed by a lone </think>
    src = "Let me work through this step by step...</think>The capital is Paris."
    assert markdown_to_speech(src) == "The capital is Paris."


def test_stray_opener_at_start_keeps_body():
    # Quantized models emit a literal <think> token at the start and never
    # close it — the body IS the answer (same policy as the display pipeline).
    src = "<think>Hello! Here is your answer."
    assert markdown_to_speech(src) == "Hello! Here is your answer."


def test_thinking_prefix_paragraph_dropped():
    src = "Thinking: the user wants X so I should do Y.\n\nHere is the answer."
    assert markdown_to_speech(src) == "Here is the answer."


def test_gemma_channel_thought_removed():
    src = "<|channel>thought\nsecret stuff\n<channel|>The answer is 42."
    out = markdown_to_speech(src)
    assert "secret stuff" not in out
    assert "The answer is 42." in out


def test_headings_become_sentences():
    out = markdown_to_speech("## Setup\nInstall it.")
    assert out.startswith("Setup.")
    assert "#" not in out


def test_table_flattened_to_sentences():
    src = "| Name | Role |\n|------|------|\n| Ada | Engineer |"
    out = markdown_to_speech(src)
    assert "|" not in out and "---" not in out
    assert "Name, Role." in out
    assert "Ada, Engineer." in out


def test_math_removed():
    out = markdown_to_speech(r"Energy is $E = mc^2$ and $$\int_0^1 x dx$$ done.")
    assert "mc^2" not in out and "\\int" not in out
    assert "Energy is" in out and "done." in out


def test_list_markers_removed_and_items_get_pauses():
    # Markers go away; each item gains sentence punctuation so engines pause
    # between items instead of reading the list as one run-on sentence.
    out = markdown_to_speech("- first\n- second\n1. third\n2) fourth")
    assert out == "first.\nsecond.\nthird.\nfourth."


def test_list_items_with_existing_punctuation_unchanged():
    out = markdown_to_speech("- Ready?\n- Go!\n- And:")
    assert out == "Ready?\nGo!\nAnd:"


def test_task_list_checkboxes_removed():
    out = markdown_to_speech("- [ ] open task\n- [x] done task")
    assert out == "open task.\ndone task."


def test_images_removed():
    assert markdown_to_speech("Look ![chart](img.png) here.") == "Look here."


def test_html_tags_removed():
    assert markdown_to_speech("a <b>bold</b> word") == "a bold word"


def test_blockquotes_and_rules_removed():
    out = markdown_to_speech("> quoted line\n\n---\n\nplain")
    assert ">" not in out and "---" not in out
    assert "quoted line" in out and "plain" in out


def test_whitespace_collapsed():
    out = markdown_to_speech("a\n\n\n\n\nb")
    assert out == "a\n\nb"


# ── Naturalization: abbreviations, symbols, punctuation ──

def test_abbreviations_expanded():
    assert markdown_to_speech("Use a cache, e.g. Redis.") == "Use a cache, for example, Redis."
    assert markdown_to_speech("CPU-bound, i.e. slow.") == "CPU-bound, that is, slow."
    assert markdown_to_speech("Python vs. Rust") == "Python versus Rust"


def test_symbols_spoken():
    assert markdown_to_speech("CPU usage hit 85%.") == "CPU usage hit 85 percent."
    assert markdown_to_speech("salt & pepper") == "salt and pepper"
    assert markdown_to_speech("It is 20°C outside.") == "It is 20 degrees Celsius outside."
    assert markdown_to_speech("Takes ~5 minutes.") == "Takes about 5 minutes."


def test_arrows_become_to():
    assert markdown_to_speech("Pipeline: input -> model -> output.") == \
        "Pipeline: input to model to output."
    assert markdown_to_speech("A → B") == "A to B"


def test_em_dash_becomes_pause():
    assert markdown_to_speech("One thing — and only one — matters.") == \
        "One thing, and only one, matters."


def test_numeric_range_en_dash():
    assert markdown_to_speech("Expect 20–60 MB.") == "Expect 20 to 60 MB."


def test_repeated_punctuation_collapsed():
    assert markdown_to_speech("Stop!!! Why??") == "Stop! Why?"


def test_snake_case_split_for_speech():
    assert markdown_to_speech("The voice af_heart is the default.") == \
        "The voice af heart is the default."


def test_emoji_stripped():
    assert markdown_to_speech("Done \U0001F680\U0001F389 see you!") == "Done see you!"
