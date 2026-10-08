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
            "json": lambda self: {"key": "PROJ-999", "id": "10001"},
            "text": "",
        })()
        calls = []

        def fake_post(url, **kw):
            calls.append({"url": url, "json": kw.get("json")})
            return resp

        monkeypatch.setattr(req_mod, "post", fake_post)
        client = jp.JiraClient("acme.atlassian.net", "me@acme.com", "tok")
        key, url = client.create_issue("PROJ", "Bug", "Fix it", "desc", {"labels": ["a"]})
        assert key == "PROJ-999"
        assert "PROJ-999" in url
        assert "/rest/api/3/issue" in calls[0]["url"]
        fields = calls[0]["json"]["fields"]
        assert fields["project"] == {"key": "PROJ"}
        assert fields["issuetype"] == {"name": "Bug"}
        assert fields["labels"] == ["a"]

    def test_create_issue_error(self, jp, monkeypatch):
        import requests as req_mod
        resp = type("R", (), {"status_code": 400, "text": "Bad Request"})()
        monkeypatch.setattr(req_mod, "post", lambda *a, **k: resp)
        client = jp.JiraClient("acme.atlassian.net", "me@acme.com", "tok")
        with pytest.raises(RuntimeError, match="Jira create failed"):
            client.create_issue("PROJ", "Bug", "x", "y")

    def test_add_comment_success(self, jp, monkeypatch):
        import requests as req_mod
        calls = []
        resp = type("R", (), {"status_code": 201, "text": ""})()

        def fake_post(url, **kw):
            calls.append({"url": url, "json": kw.get("json")})
            return resp

        monkeypatch.setattr(req_mod, "post", fake_post)
        client = jp.JiraClient("acme.atlassian.net", "me@acme.com", "tok")
        key, url = client.add_comment("PROJ-42", "A comment")
        assert key == "PROJ-42"
        assert "PROJ-42/comment" in calls[0]["url"]
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
        client.edit_issue("PROJ-42", {"summary": "New", "description": "md text"})
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
        key, url = client.transition_issue("PROJ-42", "Done")
        assert key == "PROJ-42"
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
            client.transition_issue("PROJ-42", "Released")

    def test_get_issue_success(self, jp, monkeypatch):
        import requests as req_mod
        resp = type("R", (), {
            "status_code": 200,
            "json": lambda self: {"fields": {"summary": "Test", "status": {"name": "Open"}}},
            "text": "",
        })()
        monkeypatch.setattr(req_mod, "get", lambda *a, **k: resp)
        client = jp.JiraClient("acme.atlassian.net", "me@acme.com", "tok")
        fields = client.get_issue("PROJ-42")
        assert fields["summary"] == "Test"

    def test_get_issue_404(self, jp, monkeypatch):
        import requests as req_mod
        resp = type("R", (), {"status_code": 404, "text": "Not Found"})()
        monkeypatch.setattr(req_mod, "get", lambda *a, **k: resp)
        client = jp.JiraClient("acme.atlassian.net", "me@acme.com", "tok")
        assert client.get_issue("PROJ-999") is None


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
        monkeypatch.setattr(jp, "JIRA_FIELDS", {"ea_date": "cf_ea", "ga_date": "cf_ga"})
        resp = type("R", (), {
            "status_code": 200,
            "json": lambda self: {"fields": {
                "summary": "Test", "status": {"name": "Open"},
                "duedate": None, "cf_ea": "2026-01-01", "cf_ga": None,
            }},
            "text": "",
        })()
        monkeypatch.setattr(req_mod, "get", lambda *a, **k: resp)
        result = jp.fetch_issue("TEST-42")
        assert result["status"] == "Open"
        assert result["title"] == "Test"
        assert result["ea_date"] == "2026-01-01"
        assert result["ga_date"] is None


class TestFetchChildren:
    def _resp(self, payload):
        return type("R", (), {"status_code": 200, "json": lambda self: payload, "text": ""})()

    def test_rest_maps_children_and_flags_canceled(self, jp, monkeypatch):
        import requests as req_mod
        monkeypatch.setattr(jp, "_get_client", lambda: jp.JiraClient(
            "test.atlassian.net", "me@test.com", "tok"))
        payload = {"isLast": True, "issues": [
            {"key": "PROJ-2", "fields": {
                "summary": "Opt-in setting", "issuetype": {"name": "Unit"},
                "status": {"name": "PR Review", "statusCategory": {"key": "indeterminate"}},
                "updated": "2026-10-06T14:00:00.000+0000",
                "fixVersions": [{"name": "R-2626", "releaseDate": "2026-10-09"}]}},
            {"key": "PROJ-3", "fields": {
                "summary": "Old approach", "issuetype": {"name": "Unit"},
                "status": {"name": "Canceled", "statusCategory": {"key": "done"}},
                "updated": "2026-08-01T00:00:00.000+0000", "fixVersions": []}},
        ]}
        seen = {}

        def fake_get(url, **k):
            seen["url"] = url
            seen["params"] = k.get("params")
            return self._resp(payload)
        monkeypatch.setattr(req_mod, "get", fake_get)
        kids = jp.fetch_children("PROJ-1")
        assert seen["url"].endswith("/search/jql")
        assert "parent = PROJ-1" in seen["params"]["jql"]
        assert kids[0]["key"] == "PROJ-2"
        assert kids[0]["status_category"] == "indeterminate"
        assert kids[0]["updated"] == "2026-10-06"
        assert kids[0]["fix_versions"] == [{"name": "R-2626", "release_date": "2026-10-09"}]
        assert kids[0]["canceled"] is False
        assert kids[1]["canceled"] is True

    def test_rest_search_follows_page_token(self, jp, monkeypatch):
        import requests as req_mod
        client = jp.JiraClient("test.atlassian.net", "me@test.com", "tok")
        pages = [
            {"isLast": False, "nextPageToken": "t2", "issues": [{"key": "A-1", "fields": {}}]},
            {"isLast": True, "issues": [{"key": "A-2", "fields": {}}]},
        ]
        tokens = []

        def fake_get(url, **k):
            tokens.append(k["params"].get("nextPageToken"))
            return self._resp(pages[len(tokens) - 1])
        monkeypatch.setattr(req_mod, "get", fake_get)
        out = client.search("parent = A-0")
        assert [i["key"] for i in out] == ["A-1", "A-2"]
        assert tokens == [None, "t2"]

    def test_llm_output_parsing(self, jp):
        out = ("noise\nJIRA_CHILD:PROJ-2|Unit|In Progress|indeterminate|2026-09-30|R-1@2026-10-09|Build | the thing\n"
               "JIRA_CHILD:PROJ-3|Unit|Done|done|2026-09-01|none|Shipped bit\n")
        kids = jp.parse_children_output(out)
        assert [k["key"] for k in kids] == ["PROJ-2", "PROJ-3"]
        assert kids[0]["summary"] == "Build | the thing"
        assert kids[0]["fix_versions"] == [{"name": "R-1", "release_date": "2026-10-09"}]
        assert kids[1]["fix_versions"] == []

    def test_llm_output_none_and_unparseable(self, jp):
        assert jp.parse_children_output("JIRA_CHILD:NONE") == []
        with pytest.raises(RuntimeError):
            jp.parse_children_output("I could not find it")
