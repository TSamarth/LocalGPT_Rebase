"""
Unit tests for the pure CP3 verb dispatcher (``app.cp3_adapter``).

These port v1's ``test_checkpoint.py`` CP3 cases (each verb, supplemental flag) to
the PURE adapter: instead of a monkeypatched stdin loop, we pass the full reply as
one newline-separated string and assert the deterministic ``(filtered, supplemental)``
output. No console, no editor, no model.
"""
from __future__ import annotations

from app.cp3_adapter import apply_cp3_verbs
from app.schemas import CrawlStrategy, ScoredURL


def _url(u: str, score: float = 0.5) -> ScoredURL:
    return ScoredURL(url=u, score=score, strategy=CrawlStrategy.CRAWL, etld1="x.com")


def test_done_leaves_list_unchanged():
    urls = [_url("https://a"), _url("https://b")]
    filtered, supplemental = apply_cp3_verbs(urls, "d")
    assert [u.url for u in filtered] == ["https://a", "https://b"]
    assert supplemental is False


def test_empty_reply_leaves_list_unchanged():
    urls = [_url("https://a")]
    filtered, supplemental = apply_cp3_verbs(urls, "")
    assert [u.url for u in filtered] == ["https://a"]
    assert supplemental is False


def test_add_sets_supplemental():
    filtered, supplemental = apply_cp3_verbs([_url("https://a")], "+ https://new\nd")
    assert "https://new" in [u.url for u in filtered]
    assert supplemental is True


def test_add_makes_user_sourced_max_score_crawl():
    filtered, _ = apply_cp3_verbs([], "+ https://new")
    added = filtered[-1]
    assert added.url == "https://new"
    assert added.score == 1.0
    assert added.strategy == CrawlStrategy.CRAWL
    assert added.source == "user"


def test_exclude_removes_by_index():
    filtered, supplemental = apply_cp3_verbs(
        [_url("https://a"), _url("https://b")], "- 0\nd"
    )
    assert [u.url for u in filtered] == ["https://b"]
    assert supplemental is False


def test_exclude_out_of_range_is_ignored():
    filtered, supplemental = apply_cp3_verbs([_url("https://a")], "- 5\nd")
    assert [u.url for u in filtered] == ["https://a"]
    assert supplemental is False


def test_exclude_non_digit_is_ignored():
    filtered, supplemental = apply_cp3_verbs([_url("https://a")], "- x\nd")
    assert [u.url for u in filtered] == ["https://a"]
    assert supplemental is False


def test_redirect_sets_supplemental():
    filtered, supplemental = apply_cp3_verbs([_url("https://a")], "r https://redir\nd")
    assert "https://redir" in [u.url for u in filtered]
    assert supplemental is True


def test_multiple_verbs_applied_in_order():
    # add new, then exclude index 0 (the original), then done.
    filtered, supplemental = apply_cp3_verbs(
        [_url("https://a")], "+ https://new\n- 0\nd"
    )
    assert [u.url for u in filtered] == ["https://new"]
    assert supplemental is True


def test_done_stops_processing_further_lines():
    # The line after 'd' must NOT be applied.
    filtered, supplemental = apply_cp3_verbs([_url("https://a")], "d\n+ https://late")
    assert [u.url for u in filtered] == ["https://a"]
    assert supplemental is False


def test_unrecognized_line_is_ignored():
    filtered, supplemental = apply_cp3_verbs([_url("https://a")], "garbage\nd")
    assert [u.url for u in filtered] == ["https://a"]
    assert supplemental is False
