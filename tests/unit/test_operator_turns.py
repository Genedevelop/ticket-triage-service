from evals.cases import BY_ID, CASES, OPERATOR_CASES


def test_the_label_sheet_has_a_question_and_a_correction_after_a_triaged_prefix():
    question, correction = OPERATOR_CASES
    assert (question.id, question.thread_name, question.messages_arrived) == ("OP-QUESTION", "t1", 3)
    assert (correction.id, correction.thread_name, correction.messages_arrived) == ("OP-CORRECTION", "t1", 4)
    assert question.is_previous_kept and not correction.is_previous_kept
    assert "high, not critical" in correction.operator_message
    assert (correction.urgency_accept, "critical" in correction.urgency_forbid) == ({"high"}, True)
    assert all(case.operator_message is None for case in CASES)
    assert set(BY_ID) == {case.id for case in CASES} | {"OP-QUESTION", "OP-CORRECTION", "ADV-INJECTION", "ADV-TOOL-DOWN"}
