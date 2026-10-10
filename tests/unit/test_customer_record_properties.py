from hypothesis import given
from hypothesis import strategies as st

from backend.api.routes.tickets import CustomerIn
from backend.application.customer import customer_record, split_customer
from tests.unit.strategies import models

# a customer as the ticket route hands it on: the fields that were sent, none of them null
def customers(**given):
    return models(CustomerIn, **given).map(lambda customer: customer.model_dump(exclude_none=True))


CUSTOMERS = customers()


def stored_and_read_back(customer):
    customer_id, customer_metadata = split_customer(customer)
    return customer_record({"customer_id": customer_id, "customer_metadata": customer_metadata})


@given(customers(id=st.text()))
def test_a_customer_sent_with_an_id_is_read_back_from_the_two_stored_columns_exactly_as_sent(customer):
    assert stored_and_read_back(customer) == customer


@given(customers(id=st.none()) | st.none())
def test_a_customer_sent_without_an_id_or_not_sent_is_read_back_with_a_null_id_and_everything_else_as_sent(customer):
    assert stored_and_read_back(customer) == {"id": None, **(customer or {})}


@given(CUSTOMERS)
def test_the_id_is_stored_in_its_own_column_and_never_among_the_other_fields(customer):
    customer_id, customer_metadata = split_customer(customer)

    assert customer_id == customer.get("id")
    assert customer_metadata == {name: value for name, value in customer.items() if name != "id"}
