from hypothesis import given
from hypothesis import strategies as st

from backend.domain import effect_keys
from backend.domain.ports import COMPONENTS

CHARGE_IDS = st.text(min_size=1)
REGIONS = st.text()
CONVERSATION_IDS = st.uuids()


# the same text again, or one a careless key would confuse with it: padded, in the other case, a character longer or shorter
def lookalikes(text):
    return st.sampled_from([text, f"{text} ", f" {text}", text.swapcase(), f"{text}:", f"{text}0", text[:-1]])


CHARGE_PAIRS = CHARGE_IDS.flatmap(lambda charge: st.tuples(st.just(charge), CHARGE_IDS | lookalikes(charge).filter(len)))
# a fault as the service names it: a component of the closed list and the region of the customer record, any text
FAULTS = st.tuples(st.sampled_from(COMPONENTS), REGIONS)
FAULT_PAIRS = FAULTS.flatmap(
    lambda fault: st.tuples(st.just(fault), FAULTS | st.tuples(st.sampled_from(COMPONENTS), lookalikes(fault[1])))
)


@given(CHARGE_PAIRS)
def test_two_refund_keys_are_equal_exactly_when_the_charge_is_the_same(charges):
    one, other = charges

    assert (effect_keys.refund_key(one) == effect_keys.refund_key(other)) == (one == other)


@given(st.lists(CHARGE_IDS, max_size=8).flatmap(lambda charges: st.tuples(st.just(charges), st.permutations(charges))))
def test_the_refund_key_of_a_charge_is_the_same_in_whatever_order_and_however_often_the_charges_are_proposed(proposals):
    proposed, proposed_in_another_order = proposals
    repeated = proposed_in_another_order + proposed

    keys_in_the_first_order = {charge: effect_keys.refund_key(charge) for charge in proposed}

    assert {charge: effect_keys.refund_key(charge) for charge in repeated} == keys_in_the_first_order
    assert len({effect_keys.refund_key(charge) for charge in repeated}) == len(set(proposed))


@given(FAULT_PAIRS)
def test_the_key_given_to_the_incident_system_is_equal_exactly_when_component_and_region_are_the_same(faults):
    one, other = faults

    assert (effect_keys.incident_dedup_key(*one) == effect_keys.incident_dedup_key(*other)) == (one == other)


@given(st.tuples(CONVERSATION_IDS, CONVERSATION_IDS).flatmap(lambda two: st.sampled_from([two, (two[0], two[0])])), FAULT_PAIRS)
def test_two_incident_keys_are_equal_exactly_when_conversation_component_and_region_are_the_same(conversations, faults):
    (conversation, other_conversation), (fault, other_fault) = conversations, faults
    are_equal = effect_keys.incident_key(conversation, *fault) == effect_keys.incident_key(other_conversation, *other_fault)

    assert are_equal == ((conversation, fault) == (other_conversation, other_fault))


@given(CONVERSATION_IDS, CONVERSATION_IDS, FAULTS)
def test_two_conversations_reporting_one_fault_hold_different_keys_here_and_give_the_incident_system_the_same_one(
    conversation, other_conversation, fault
):
    if conversation != other_conversation:
        assert effect_keys.incident_key(conversation, *fault) != effect_keys.incident_key(other_conversation, *fault)

    assert effect_keys.incident_key(conversation, *fault).endswith(effect_keys.incident_dedup_key(*fault))
    assert effect_keys.incident_key(other_conversation, *fault).endswith(effect_keys.incident_dedup_key(*fault))

