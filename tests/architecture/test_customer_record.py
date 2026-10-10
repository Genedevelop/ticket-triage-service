from backend.application import customer, model_input
from tests.architecture.test_import_rule import backend_modules, within


def test_the_customer_record_is_split_and_joined_by_a_module_that_does_not_reach_the_store():
    customer_id, rest = customer.split_customer({"id": "cus_1", "region": "EU"})

    assert (customer_id, rest) == ("cus_1", {"region": "EU"})
    assert customer.customer_record({"customer_id": customer_id, "customer_metadata": rest}) == {"id": "cus_1", "region": "EU"}
    assert customer.customer_record({"customer_id": None, "customer_metadata": None}) == {"id": None}
    assert model_input.customer_record is customer.customer_record
    imported = dict(backend_modules())
    assert [
        name
        for module in ("backend.application.customer", "backend.application.model_input")
        for name in imported[module]
        if within(name, "backend.infrastructure") or within(name, "backend.application.conversations")
    ] == []
