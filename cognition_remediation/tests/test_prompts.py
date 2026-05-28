import pytest
from app.github_client import Issue
from app.prompts import definite_prompt, semi_definite_prompt, _slug


@pytest.mark.unit
def test_slug_lowercases_and_replaces_spaces():
    assert _slug("Fix CVE-2023-45803") == "fix-cve-2023-45803"


@pytest.mark.unit
def test_slug_truncates_at_40_chars():
    assert len(_slug("a" * 100)) <= 40


@pytest.mark.unit
def test_definite_prompt_includes_issue_number_and_title():
    issue = Issue(number=42, title="Upgrade urllib3", labels=[], body="Upgrade to fix CVE.")
    result = definite_prompt(issue)
    assert "#42" in result
    assert "Upgrade urllib3" in result


@pytest.mark.unit
def test_definite_prompt_includes_body():
    issue = Issue(number=1, title="T", labels=[], body="Specific remediation steps here.")
    result = definite_prompt(issue)
    assert "Specific remediation steps here." in result


@pytest.mark.unit
def test_definite_prompt_specifies_branch_name():
    issue = Issue(number=5, title="Fix the bug", labels=[], body="")
    result = definite_prompt(issue)
    assert "fix/5-fix-the-bug" in result


@pytest.mark.unit
def test_semi_definite_prompt_includes_issue_number_and_title():
    issue = Issue(number=10, title="Unicode filter bug", labels=[], body="Details here.")
    result = semi_definite_prompt(issue)
    assert "#10" in result
    assert "Unicode filter bug" in result


@pytest.mark.unit
def test_semi_definite_prompt_asks_to_document_root_cause():
    issue = Issue(number=1, title="T", labels=[], body="")
    result = semi_definite_prompt(issue)
    assert "root cause" in result.lower()
