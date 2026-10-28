"""The four read-only tools, run against the real clone at the pinned SHA."""
from __future__ import annotations

import pytest

from agent import tools

CSS = "packages/vite/src/node/plugins/css.ts"
ESBUILD = "packages/vite/src/node/plugins/esbuild.ts"


def test_list_dir_marks_directories():
    assert "packages/vite/src/node/plugins/" in tools.list_dir("packages/vite/src/node")


def test_read_file_caps_at_400_lines_and_reports_length():
    out = tools.read_file(CSS)
    assert "lines 1-400 of 3685" in out.splitlines()[0]
    assert "\n400: " in out
    assert "\n401" not in out


def test_read_file_range():
    assert "lines 10-20 of 3685" in tools.read_file(CSS, 10, 20)


def test_read_file_inverted_range_is_an_error():
    assert tools.read_file(CSS, 20, 10).startswith("Error: ")


def test_read_file_past_end_is_an_error():
    out = tools.read_file(CSS, 5000)
    assert out.startswith("Error: ") and "3685" in out


def test_read_file_on_a_directory_is_an_error():
    assert tools.read_file("packages/vite/src/node").startswith("Error: ")


def test_list_dir_on_a_file_is_an_error():
    assert tools.list_dir(CSS).startswith("Error: ")


def test_missing_path_is_an_error():
    assert tools.read_file("packages/nope.ts").startswith("Error: ")


@pytest.mark.parametrize("bad", ["../etc/passwd", "/etc/passwd", "packages/../../x", "-rf"])
def test_unsafe_paths_are_refused(bad):
    assert tools.read_file(bad).startswith("Error: ")


def test_pattern_starting_with_dash_is_searched_not_parsed_as_an_option():
    assert not tools.grep("--output=x", "packages/vite/src").startswith("Error: ")


def test_invalid_regex_is_an_error():
    assert tools.grep("(", "packages/vite/src").startswith("Error: ")


def test_grep_caps_matches_and_reports_total():
    out = tools.grep("import", "packages/vite/src")
    assert out.count("\n") <= 51
    first = out.splitlines()[0]
    assert first.startswith("showing 50 of ") and first.endswith(" matches")


def test_grep_no_matches_is_not_an_error():
    assert tools.grep("zzqqxx_no_such_text", "packages/vite/src") == "showing 0 of 0 matches"


def test_git_log_hides_the_fix_before_the_issue_was_filed():
    assert "#13909" not in tools.git_log(ESBUILD, 30, cutoff="2023-07-20T10:52:02Z")


def test_git_log_shows_it_after():
    assert "#13909" in tools.git_log(ESBUILD, 30, cutoff="2023-07-22T00:00:00Z")


def test_execute_flags_errors():
    args = {"path": "nope.ts", "start_line": 1, "end_line": None}
    assert tools.execute("read_file", args, cutoff="2030-01-01")[1] is True


def test_execute_unknown_tool_is_an_error():
    assert tools.execute("rm", {}, cutoff="2030-01-01")[1] is True


def test_execute_truncates_long_output(monkeypatch):
    monkeypatch.setattr(tools, "MAX_CHARS", 100)
    out, is_error = tools.execute("grep", {"pattern": ".", "path": "packages/vite/src"}, cutoff="2030-01-01")
    assert not is_error
    assert out.endswith("[truncated]")
    assert len(out) <= 100 + len("\n… [truncated]")


def test_split_lines_splits_on_newline_only():
    assert tools._split_lines("a\x0cb\nc\u2028d\n") == ["a\x0cb", "c\u2028d"]
    assert tools._split_lines("") == []


def test_grep_line_numbers_agree_with_read_file():
    hit = tools.grep("export default", CSS).splitlines()[1]
    _, n, text = hit.split(":", 2)
    assert f"\n{n}: {text}" in tools.read_file(CSS, max(1, int(n) - 1), int(n) + 1)


def test_git_times_out_as_error(monkeypatch):
    monkeypatch.setattr(tools, "GIT_TIMEOUT", 0.001)
    out, is_error = tools.execute("grep", {"pattern": "."}, cutoff="2030-01-01")
    assert is_error and "timed out after" in out


def test_pathspec_magic_is_literal():
    g = tools.grep("import", ":(top)packages")
    assert g == "showing 0 of 0 matches" or g.startswith("Error: ")
    log = tools.git_log(":(top)packages", cutoff="2030-01-01")
    assert log.startswith(("Error: ", "No commits"))


def test_tool_specs_are_strict():
    assert all(
        s["strict"] and set(s["input_schema"]["required"]) == set(s["input_schema"]["properties"])
        and s["input_schema"]["additionalProperties"] is False
        for s in tools.TOOL_SPECS
    )
