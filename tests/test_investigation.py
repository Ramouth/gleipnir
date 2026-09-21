from datetime import date

import pytest

from gleipnir.investigation import (
    AnswerKind, EvidenceProposal, InvestigationError, QuestionContract,
    QuestionRun, ReviewOutcome, ReviewPacket, ScreenMandate, store_review_packet,
)
from gleipnir.predicates.core import V
from gleipnir.rawstore import RawStore


AS_OF = date(2026, 8, 28)


def mandate(**changes):
    data = dict(id="m-1", subject_key="12345678", purpose="defence supplier diligence",
                consent_ref="a" * 64, granted_on=date(2026, 8, 1),
                expires_on=date(2026, 9, 1), permitted_sources=("statstidende",),
                permitted_subject_kinds=("person",))
    return ScreenMandate(**(data | changes))


def contract(**changes):
    data = dict(id="officer-prior-insolvency", version="1", question=(
                    "Was this person an officer when the company entered bankruptcy?"),
                trigger_predicates=("dissolution_threat_on_file",), source="statstidende",
                subject_kind="person", required_fields=("person", "role_from", "event_date"),
                answer_kind=AnswerKind.BOOLEAN,
                decision_effect="opens a human-reviewed corporate-history thread",
                coverage_rule="UNKNOWABLE when the official notice or role interval is absent")
    return QuestionContract(**(data | changes))


def test_contract_must_name_data_that_answers_it():
    with pytest.raises(InvestigationError, match="answer fields"):
        contract(required_fields=()).validate()


def test_question_run_is_mandated_triggered_and_evidenced():
    proposal = EvidenceProposal("officer-prior-insolvency", "1", "b" * 64,
                                "statstidende", "P was a director", {"role": "director"})
    run = QuestionRun.create(mandate=mandate(), contract=contract(), subject_key="p:1",
                             as_of=AS_OF, trigger_claim_refs=("c" * 64,), answer=V.TRUE,
                             proposals=(proposal,))
    assert run.answer is V.TRUE and run.contract_id == "officer-prior-insolvency"


def test_question_run_rejects_unmandated_or_unsupported_answers():
    with pytest.raises(InvestigationError, match="mandate"):
        QuestionRun.create(mandate=mandate(expires_on=date(2026, 8, 27)),
                           contract=contract(), subject_key="p:1", as_of=AS_OF,
                           trigger_claim_refs=("c",), answer=V.UNKNOWN)
    with pytest.raises(InvestigationError, match="requires evidence"):
        QuestionRun.create(mandate=mandate(), contract=contract(), subject_key="p:1",
                           as_of=AS_OF, trigger_claim_refs=("c",), answer=V.FALSE)


def test_model_proposal_must_quote_its_source():
    p = EvidenceProposal("q", "1", "a" * 64, "statstidende", "not in source", {})
    with pytest.raises(InvestigationError, match="quote"):
        p.verify_against("The source says something else")


def test_review_packet_is_human_only_and_never_stores_a_belief_label():
    human = contract(requires_human_review=True)
    packet = ReviewPacket.create(mandate=mandate(), contract=human, subject_key="p:1",
                                 as_of=AS_OF, raw_ref="d" * 64,
                                 quote="named organisation", context="Article named organisation.",
                                 reason="returned by approved official-source question")
    assert packet.outcome is ReviewOutcome.PENDING
    reviewed = packet.review(outcome=ReviewOutcome.IRRELEVANT, reviewer="analyst-a",
                             rationale="No objective security fact is stated.")
    assert reviewed.outcome is ReviewOutcome.IRRELEVANT
    assert "politic" not in repr(reviewed).casefold()
    with pytest.raises(InvestigationError, match="immutable"):
        reviewed.review(outcome=ReviewOutcome.ESCALATE_LEGAL, reviewer="analyst-b",
                        rationale="second opinion")


def test_review_packet_is_append_only_in_raw_store(tmp_path):
    packet = ReviewPacket.create(mandate=mandate(), contract=contract(requires_human_review=True),
                                 subject_key="p:1", as_of=AS_OF, raw_ref="d" * 64,
                                 quote="named organisation", context="Article named organisation.",
                                 reason="approved source")
    store = RawStore(tmp_path)
    first = store_review_packet(store, packet)
    second = store_review_packet(store, packet.review(
        outcome=ReviewOutcome.IDENTITY_UNRESOLVED, reviewer="analyst-a",
        rationale="The source does not identify the person."))
    assert first != second
    assert [r.resource_type for r in store.fetches()] == ["review_packet", "review_packet"]
