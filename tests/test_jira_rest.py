"""Tests for Jira direct REST API client and markdown-to-ADF conversion."""
import json
import pytest


@pytest.fixture
def jp():
    import jira_publish
    return jira_publish


class TestMarkdownToAdf:
    def test_empty_string(self, jp):
        adf = jp._markdown_to_adf("")
        assert adf["type"] == "doc"
        assert adf["version"] == 1
        assert len(adf["content"]) == 1

    def test_single_paragraph(self, jp):
        adf = jp._markdown_to_adf("Hello world")
        assert adf["content"][0]["type"] == "paragraph"
        assert adf["content"][0]["content"][0]["text"] == "Hello world"

    def test_bold_text(self, jp):
        adf = jp._markdown_to_adf("This is **bold** text")
        nodes = adf["content"][0]["content"]
        assert nodes[0]["text"] == "This is "
        assert nodes[1]["text"] == "bold"
        assert nodes[1]["marks"] == [{"type": "strong"}]
        assert nodes[2]["text"] == " text"

    def test_heading(self, jp):
        adf = jp._markdown_to_adf("### Section Title")
        node = adf["content"][0]
        assert node["type"] == "heading"
        assert node["attrs"]["level"] == 3
        assert node["content"][0]["text"] == "Section Title"

    def test_heading_levels(self, jp):
        for level in range(1, 7):
            hashes = "#" * level
            adf = jp._markdown_to_adf(f"{hashes} Title")
            assert adf["content"][0]["attrs"]["level"] == level

    def test_bullet_list(self, jp):
        adf = jp._markdown_to_adf("- item one\n- item two\n- item three")
        bl = adf["content"][0]
        assert bl["type"] == "bulletList"
        assert len(bl["content"]) == 3
        assert bl["content"][0]["type"] == "listItem"
        assert bl["content"][0]["content"][0]["content"][0]["text"] == "item one"

    def test_asterisk_bullets(self, jp):
        adf = jp._markdown_to_adf("* first\n* second")
        assert adf["content"][0]["type"] == "bulletList"
        assert len(adf["content"][0]["content"]) == 2

    def test_mixed_content(self, jp):
        md = "### Heading\n\nA paragraph.\n\n- item a\n- item b"
        adf = jp._markdown_to_adf(md)
        types = [n["type"] for n in adf["content"]]
        assert "heading" in types
        assert "paragraph" in types
        assert "bulletList" in types


class TestJiraClient:
    def test_create_issue_success(self, jp, monkeypatch):
        import requests as req_mod
        resp = type("R", (), {
            "status_code": 201,
            "json": lambda self: {"key": "VNT-999", "id": "10001"},
            "text": "",
        })()
        calls = []

        def fake_post(url, **kw):
            calls.append({"url": url, "json": kw.get("json")})
            return resp

        monkeypatch.setattr(req_mod, "post", fake_post)
        client = jp.JiraClient("acme.atlassian.net", "me@acme.com", "tok")
        key, url = client.create_issue("VNT", "Bug", "Fix it", "desc", {"labels": ["a"]})
        assert key == "VNT-999"
        assert "VNT-999" in url
        assert "/rest/api/3/issue" in calls[0]["url"]
        fields = calls[0]["json"]["fields"]
        assert fields["project"] == {"key": "VNT"}
        assert fields["issuetype"] == {"name": "Bug"}
        assert fields["labels"] == ["a"]

    def test_create_issue_error(self, jp, monkeypatch):
        import requests as req_mod
        resp = type("R", (), {"status_code": 400, "text": "Bad Request"})()
        monkeypatch.setattr(req_mod, "post", lambda *a, **k: resp)
        client = jp.JiraClient("acme.atlassian.net", "me@acme.com", "tok")
        with pytest.raises(RuntimeError, match="Jira create failed"):
            client.create_issue("VNT", "Bug", "x", "y")

    def test_add_comment_success(self, jp, monkeypatch):
        import requests as req_mod
        calls = []
        resp = type("R", (), {"status_code": 201, "text": ""})()

        def fake_post(url, **kw):
            calls.append({"url": url, "json": kw.get("json")})
            return resp

        monkeypatch.setattr(req_mod, "post", fake_post)
        client = jp.JiraClient("acme.atlassian.net", "me@acme.com", "tok")
        key, url = client.add_comment("VNT-42", "A comment")
        assert key == "VNT-42"
        assert "VNT-42/comment" in calls[0]["url"]
        assert calls[0]["json"]["body"]["type"] == "doc"

    def test_edit_issue_converts_description(self, jp, monkeypatch):
        import requests as req_mod
        calls = []
        resp = type("R", (), {"status_code": 204, "text": ""})()

        def fake_put(url, **kw):
            calls.append({"url": url, "json": kw.get("json")})
            return resp

        monkeypatch.setattr(req_mod, "put", fake_put)
        client = jp.JiraClient("acme.atlassian.net", "me@acme.com", "tok")
        client.edit_issue("VNT-42", {"summary": "New", "description": "md text"})
        fields = calls[0]["json"]["fields"]
        assert fields["summary"] == "New"
        assert fields["description"]["type"] == "doc"

    def test_transition_exact_match(self, jp, monkeypatch):
        import requests as req_mod
        transitions_resp = type("R", (), {
            "status_code": 200,
            "json": lambda self: {"transitions": [
                {"id": "31", "name": "In Progress"},
                {"id": "41", "name": "Done"},
            ]},
            "text": "",
        })()
        post_resp = type("R", (), {"status_code": 204, "text": ""})()
        calls = []

        def fake_get(url, **kw):
            return transitions_resp

        def fake_post(url, **kw):
            calls.append(kw.get("json"))
            return post_resp

        monkeypatch.setattr(req_mod, "get", fake_get)
        monkeypatch.setattr(req_mod, "post", fake_post)
        client = jp.JiraClient("acme.atlassian.net", "me@acme.com", "tok")
        key, url = client.transition_issue("VNT-42", "Done")
        assert key == "VNT-42"
        assert calls[0]["transition"]["id"] == "41"

    def test_transition_no_match(self, jp, monkeypatch):
        import requests as req_mod
        resp = type("R", (), {
            "status_code": 200,
            "json": lambda self: {"transitions": [{"id": "31", "name": "In Progress"}]},
            "text": "",
        })()
        monkeypatch.setattr(req_mod, "get", lambda *a, **k: resp)
        client = jp.JiraClient("acme.atlassian.net", "me@acme.com", "tok")
        with pytest.raises(RuntimeError, match="No matching transition"):
            client.transition_issue("VNT-42", "Released")

    def test_get_issue_success(self, jp, monkeypatch):
        import requests as req_mod
        resp = type("R", (), {
            "status_code": 200,
            "json": lambda self: {"fields": {"summary": "Test", "status": {"name": "Open"}}},
            "text": "",
        })()
        monkeypatch.setattr(req_mod, "get", lambda *a, **k: resp)
        client = jp.JiraClient("acme.atlassian.net", "me@acme.com", "tok")
        fields = client.get_issue("VNT-42")
        assert fields["summary"] == "Test"

    def test_get_issue_404(self, jp, monkeypatch):
        import requests as req_mod
        resp = type("R", (), {"status_code": 404, "text": "Not Found"})()
        monkeypatch.setattr(req_mod, "get", lambda *a, **k: resp)
        client = jp.JiraClient("acme.atlassian.net", "me@acme.com", "tok")
        assert client.get_issue("VNT-999") is None


class TestDispatchRouting:
    def test_publish_uses_rest_when_token_set(self, jp, monkeypatch):
        import requests as req_mod
        monkeypatch.setattr(jp, "JIRA_PROJECT_KEY", "TEST")
        monkeypatch.setattr(jp, "JIRA_COMPONENT_ID", "999")
        monkeypatch.setattr(jp, "JIRA_AUTO_LABEL", "")
        monkeypatch.setattr(jp, "JIRA_DEFAULT_ASSIGNEE", "")
        monkeypatch.setattr(jp, "_get_client", lambda: jp.JiraClient(
            "test.atlassian.net", "me@test.com", "tok"))
        resp = type("R", (), {
            "status_code": 201,
            "json": lambda self: {"key": "TEST-1"},
            "text": "",
        })()
        monkeypatch.setattr(req_mod, "post", lambda *a, **k: resp)
        key, url = jp.publish_to_jira({
            "type": "Bug", "summary": "Fix it",
            "description": "Broken", "labels": [], "priority": "",
        })
        assert key == "TEST-1"

    def test_publish_falls_back_to_llm_when_no_token(self, jp, monkeypatch):
        monkeypatch.setattr(jp, "_get_client", lambda: None)
        called = []
        monkeypatch.setattr(jp, "_publish_llm",
                            lambda d, s=None: called.append(1) or ("KEY-1", "url"))
        jp.publish_to_jira({"summary": "x", "description": "y"})
        assert called == [1]

    def test_fetch_uses_rest_when_token_set(self, jp, monkeypatch):
        import requests as req_mod
        monkeypatch.setattr(jp, "_get_client", lambda: jp.JiraClient(
            "test.atlassian.net", "me@test.com", "tok"))
        resp = type("R", (), {
            "status_code": 200,
            "json": lambda self: {"fields": {
                "summary": "Test", "status": {"name": "Open"},
                "duedate": None, "customfield_10683": None, "customfield_10300": None,
            }},
            "text": "",
        })()
        monkeypatch.setattr(req_mod, "get", lambda *a, **k: resp)
        result = jp.fetch_issue("TEST-42")
        assert result["status"] == "Open"
        assert result["title"] == "Test"
