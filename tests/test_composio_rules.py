"""What a Composio step is (a read, a draft, a write into a sheet or doc, an act on people), judged from its name and target."""
import pytest

from minty_core.composio_rules import artifact_ids, call_class, created_artifacts, step_kind

SHEET = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"
DOC = "1a2B3c4D5e6F7g8H9i0J1k2L3m4N5o6P7q8R9s0T1u2V"


@pytest.mark.parametrize("slug, kind", [
    ("GMAIL_FETCH_EMAILS", "read"), ("GMAIL_LIST_HISTORY", "read"), ("GMAIL_GET_PROFILE", "read"), ("GITHUB_GET_THE_AUTHENTICATED_USER", "read"),
    ("GOOGLESHEETS_VALUES_GET", "read"), ("GOOGLESHEETS_GET_SHEET_NAMES", "read"), ("GOOGLECALENDAR_CALENDAR_LIST_GET", "read"),
    ("GOOGLECALENDAR_FIND_FREE_SLOTS", "read"), ("GOOGLEDOCS_GET_DOCUMENT_BY_ID", "read"),
    ("GMAIL_CREATE_EMAIL_DRAFT", "draft"), ("GMAIL_UPDATE_DRAFT", "draft"),
    ("GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND", "artifact"), ("GOOGLESHEETS_UPDATE_VALUES_BATCH", "artifact"), ("GOOGLESHEETS_FORMAT_CELL", "artifact"),
    ("GOOGLESHEETS_CREATE_GOOGLE_SHEET1", "artifact"), ("GOOGLEDOCS_CREATE_DOCUMENT", "artifact"), ("GOOGLEDOCS_UPDATE_DOCUMENT_MARKDOWN", "artifact"),
    ("GMAIL_SEND_EMAIL", "act"), ("GMAIL_REPLY_TO_THREAD", "act"), ("GMAIL_SEND_DRAFT", "act"), ("GMAIL_ADD_LABEL_TO_EMAIL", "act"),
    ("GOOGLESHEETS_CLEAR_VALUES", "act"), ("GOOGLEDOCS_DELETE_CONTENT_RANGE", "act"), ("GOOGLECALENDAR_CREATE_EVENT", "act"),
    ("GITHUB_CREATE_AN_ISSUE", "act"), ("GITHUB_GET_AND_MERGE_PR", "act"), ("GMAIL_FETCH_AND_DELETE_EMAILS", "act"),
    ("SOMETHING_ODD", "act"), ("", "act"), ("gmail_fetch_emails", "read"),
])
def test_a_step_is_judged_by_its_name(slug, kind):
    assert step_kind(slug) == kind


def steps(*pairs):
    return {"tools": [{"tool_slug": slug, "arguments": args} for slug, args in pairs]}


def test_reads_and_drafts_are_class_1_anything_that_acts_is_class_3():
    known = lambda ext_id: False  # noqa: E731
    assert call_class(steps(("GMAIL_FETCH_EMAILS", {}), ("GOOGLESHEETS_VALUES_GET", {"spreadsheet_id": SHEET})), known) == 1
    assert call_class(steps(("GMAIL_CREATE_EMAIL_DRAFT", {"recipient_email": "a@b.co"})), known) == 1
    assert call_class(steps(("GMAIL_FETCH_EMAILS", {}), ("GMAIL_SEND_EMAIL", {})), known) == 3  # one step that acts asks about all
    assert call_class(steps(("SOMETHING_ODD", {})), known) == 3
    assert call_class({}, known) == 3 and call_class({"tools": "GMAIL_FETCH_EMAILS"}, known) == 3 and call_class({"tools": []}, known) == 3


def test_writing_into_a_sheet_or_doc_is_class_2_only_when_the_target_is_one_minty_knows():
    known = lambda ext_id: ext_id == SHEET  # noqa: E731
    append = steps(("GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND", {"spreadsheetId": SHEET, "values": [["a"]]}))
    assert call_class(append, known) == 2
    assert call_class(steps(("GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND", {"spreadsheetId": DOC})), known) == 3  # somebody else's sheet
    assert call_class(steps(("GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND", {"values": [["a"]]})), known) == 3  # no target named: cannot tell
    assert call_class(steps(("GOOGLESHEETS_CREATE_GOOGLE_SHEET1", {"title": "Leads"})), known) == 2  # a new one has no target yet
    assert call_class(steps(("GOOGLEDOCS_CREATE_DOCUMENT", {"title": "Notes"}), ("GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND", {"spreadsheetId": SHEET})), known) == 2
    assert call_class(steps(("GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND", {"spreadsheetId": SHEET}), ("GMAIL_SEND_EMAIL", {})), known) == 3
    assert call_class(steps(("GOOGLESHEETS_CLEAR_VALUES", {"spreadsheetId": SHEET})), known) == 3  # deleting data is never a quiet write


def test_the_ids_a_step_names_are_found_whatever_the_argument_is_called():
    assert artifact_ids({"spreadsheet_id": SHEET}) == [SHEET]
    assert artifact_ids({"spreadsheetId": SHEET, "range": "A1"}) == [SHEET]
    assert artifact_ids({"document_id": DOC}) == [DOC]
    assert artifact_ids({"documentId": DOC}) == [DOC]
    assert artifact_ids({"spreadsheet_id": "not an id!"}) == ["not an id!"]  # whatever it is, the registry will not know it
    assert artifact_ids({"range": "A1"}) == []


def test_what_a_create_step_made_is_read_from_its_answer():
    sheet_url = f"https://docs.google.com/spreadsheets/d/{SHEET}/edit"
    answer = '{"successful":true,"data":{"spreadsheetUrl":"%s","properties":{"title":"Leads"}}}' % sheet_url
    made = created_artifacts(steps(("GOOGLESHEETS_CREATE_GOOGLE_SHEET1", {"title": "Leads"})), answer)
    assert made == [("sheet", SHEET, sheet_url, "Leads")]
    doc = created_artifacts(steps(("GOOGLEDOCS_CREATE_DOCUMENT", {"title": "Notes"})), f"see https://docs.google.com/document/d/{DOC}/edit please")
    assert doc == [("doc", DOC, f"https://docs.google.com/document/d/{DOC}/edit", "Notes")]
    assert created_artifacts(steps(("GMAIL_FETCH_EMAILS", {})), answer) == []  # only a step that creates one counts
    assert created_artifacts(steps(("GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND", {"spreadsheetId": SHEET})), answer) == []
    assert created_artifacts(steps(("GOOGLESHEETS_CREATE_GOOGLE_SHEET1", {})), "no link in here") == []
    assert created_artifacts(steps(("GOOGLESHEETS_CREATE_GOOGLE_SHEET1", {"title": "x"})), answer + answer) == [("sheet", SHEET, sheet_url, "x")]  # once
