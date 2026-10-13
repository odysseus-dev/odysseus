from src.search_passages import search_excerpt


def test_late_relevant_passage_survives_budget_and_preserves_negation():
    text = ('Archived overview; check current documentation. ' + 'Browser navigation and features. ' * 180
            + 'Privacy Guide does not block every tracker. It explains cookie settings and their limitations. '
            + 'Other unrelated material. ' * 100)
    result = search_excerpt(text, 'browser privacy cookie settings', 1200)
    assert len(result) <= 1200
    assert result.startswith('Archived overview;')
    assert 'Privacy Guide does not block every tracker.' in result
    assert 'text omitted' in result


def test_short_evidence_is_not_modified():
    text = 'A small complete source.'
    assert search_excerpt(text, 'source', 500) == text


def test_no_match_uses_bounded_explicitly_incomplete_opening():
    result = search_excerpt('Other material. ' * 300, 'privacy', 500)
    assert len(result) <= 500
    assert result.startswith('Other material.')
    assert 'text omitted' in result


def test_distinct_topics_retain_original_order_and_literal_text():
    text = ('Introduction. ' + 'Filler. ' * 100 + 'Alpha evidence is limited. ' + 'Filler. ' * 200
            + 'Beta evidence is preliminary. ' + 'Filler. ' * 200)
    result = search_excerpt(text, 'alpha beta', 1000)
    assert 'Alpha evidence is limited.' in result
    assert 'Beta evidence is preliminary.' in result
    assert result.index('Alpha evidence') < result.index('Beta evidence')


def test_relevant_evidence_survives_both_search_and_observation_caps():
    text = 'Page introduction. ' + 'Navigation and performance features. ' * 180
    text += 'Privacy Guide explains how to choose cookie settings. These do not prevent every form of tracking. '
    text += 'Other features and links. ' * 200
    first = search_excerpt(text, 'browser privacy cookie settings', 3000)
    final = search_excerpt(first, 'browser privacy cookie settings', 1100)
    assert len(final) <= 1100
    assert 'Privacy Guide explains how to choose cookie settings.' in final
    assert 'These do not prevent every form of tracking.' in final
