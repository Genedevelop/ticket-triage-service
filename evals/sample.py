import json
import sys
from datetime import datetime, timedelta, timezone

from backend.infrastructure.providers.sample_data import THREADS

EXIT_USAGE = 2


def request_body(thread_name: str, now: datetime) -> dict:
    thread = THREADS[thread_name]
    return {
        "customer": thread["customer"],
        "messages": [
            {"author": "customer", "body": body, "sent_at": (now - timedelta(minutes=minutes_ago)).isoformat()}
            for minutes_ago, body in thread["messages"]
        ],
    }


def main(argv: list[str]) -> int:
    # Step 1: Refuse anything but the name of one sample thread
    if len(argv) != 2 or argv[1] not in THREADS:
        print("usage: python -m evals.sample t1|t2|t3", file=sys.stderr)
        return EXIT_USAGE
    # Step 2: Print the request body with message times counted back from this moment
    print(json.dumps(request_body(argv[1], datetime.now(timezone.utc)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
