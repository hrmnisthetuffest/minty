"""How big is this goal, and what would make the result genuinely useful? Plain rules first, a model only where they cannot say."""
from minty_core.workflow import brief

BIG = ("Find 20 promising companies in this market, investigate them, organize their information in a spreadsheet, rank the best "
       "opportunities, and prepare personalized outreach")


def test_a_simple_question_gets_a_direct_answer_and_nothing_extra():
    for goal in ("what is the capital of france", "send this email to alex", "what time is it in tokyo", "remind me to call mom"):
        b = brief(goal, can_sheet=True)
        assert b.kind in ("answer", "task") and b.deliverables == () and b.text() == "", goal


def test_a_big_research_goal_becomes_a_project_with_a_sheet_a_ranking_and_follow_ups():
    b = brief(BIG, can_sheet=True)
    assert b.kind == "project" and {"sheet", "follow-ups"} <= set(b.deliverables)
    text = b.text()
    assert "artifacts_list" in text and "read back" in text.lower() and "source" in text and "do not ask" in text.lower()
    assert "Do not create anything the goal does not need" in text


def test_without_a_spreadsheet_tool_it_says_so_instead_of_pretending():
    b = brief(BIG, can_sheet=False)
    assert "sheet" not in b.deliverables
    assert "no spreadsheet tool" in b.text().lower() and "say plainly" in b.text().lower()


def test_a_comparison_or_a_lead_list_is_a_working_table_even_if_the_words_sheet_and_spreadsheet_never_appear():
    assert "sheet" in brief("research the top 10 competitors of acme and compare them", can_sheet=True).deliverables
    assert "sheet" in brief("build me a lead list of dentists in pune", can_sheet=True).deliverables


def test_a_report_is_a_document_and_deadlines_become_reminders():
    assert "doc" in brief("research solar panels and write up a report for the family", can_sheet=True).deliverables
    assert "reminders" in brief("investigate the visa options and note every deadline", can_sheet=True).deliverables


def test_what_the_user_prefers_can_add_a_sheet_but_never_more_than_the_goal_needs():
    one = "research the best standing desks"
    assert "sheet" not in brief(one, can_sheet=True).deliverables
    assert "sheet" in brief(one, can_sheet=True, prefers=lambda what: what == "research->sheet").deliverables
    assert brief("what is two plus two", can_sheet=True, prefers=lambda what: True).deliverables == ()  # a preference never turns a question into a project


def test_it_is_cheap_and_repeatable():
    import time
    start = time.perf_counter()
    first = brief(BIG, can_sheet=True)
    for _ in range(200):
        assert brief(BIG, can_sheet=True) == first
    assert time.perf_counter() - start < 0.5
