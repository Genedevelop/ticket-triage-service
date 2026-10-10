
def refund_key(charge_id: str) -> str:
    return f"refund:{charge_id}"


def incident_key(conversation_id: object, component: str, region: str) -> str:
    return f"incident:{conversation_id}:{component}:{region}"


def incident_dedup_key(component: str, region: str) -> str:
    return f"{component}:{region}"
