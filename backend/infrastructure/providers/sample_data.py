from datetime import datetime, timedelta

from backend.domain.ports import COMPONENTS, Providers

# the three sample tickets: the customer record, and each message with how many minutes ago it was sent
THREADS = {
    "t1": {
        "customer": {"id": "cust_t1", "plan": "free", "tenure_months": 4, "prior_tickets": 0},
        "messages": [
            (180, "My payment failed when I tried to upgrade to Pro. Can you check what's wrong?"),
            (120, "I tried again with a different card. Now I see TWO pending charges but my account still shows Free plan??"),
            (60, "Okay this is getting ridiculous. Just checked my bank app — I have THREE charges of $29.99 now. None refunded. And I STILL don't have Pro access."),
            (0, "HELLO?? Is anyone there??? I need this fixed NOW. I have a presentation in 2 hours and need the Pro export features. If these charges aren't reversed by end of day I'm disputing all of them with my bank."),
        ],
    },
    "t2": {
        "customer": {"id": "cust_t2", "plan": "enterprise", "region": "thailand", "seats": 45, "tenure_months": 8},
        "messages": [
            (120, "ระบบเข้าไม่ได้ครับ ขึ้น error 500"),
            (90, "ลองหลายเครื่องแล้ว ทั้ง Chrome, Safari, Firefox ผลเหมือนกันหมด เพื่อนร่วมงานก็เข้าไม่ได้เหมือนกัน"),
            (45, "ตอนนี้ลูกค้าโวยเข้ามาเยอะมาก เรามี demo กับลูกค้ารายใหญ่บ่ายนี้ ถ้าระบบไม่กลับมา deal นี้อาจจะหลุด"),
            (0, "เช็ค status.company.com แล้ว บอกว่า all systems operational แต่เราใช้งานไม่ได้จริงๆ region Asia มีปัญหาหรือเปล่า?"),
        ],
    },
    "t3": {
        "customer": {"id": "cust_t3", "plan": "pro", "tenure_months": 5, "prior_tickets": 0},
        "messages": [
            (2880, "Hey, just wondering if you support dark mode? No rush 😊"),
            (1440, "Thanks! Found Settings > Appearance. But I'm on Pro and only see 'Light' and 'System Default' — no dark mode toggle?"),
            (1380, "Switched to 'System Default', my Mac is in dark mode, but your app still shows light theme. Bug, or am I missing something?"),
            (30, "Random one while I have you — any way to schedule dark mode, auto-switch at 6pm? Would be cool 👀"),
        ],
    },
}

REGIONS = ("us", "eu", "asia")
# a customer no ticket is about, with one captured charge no ticket may touch
BYSTANDER = ("cust_bystander", "ch_bystander_1")
STATUS_PAGE_AGE_MINUTES = 20
CHARGE_AMOUNT_CENTS = 2999
MINUTES_PER_DAY = 1440

# each customer's billing account; minutes count back from the thread's last message
ACCOUNTS = {
    "t1": {"plan": "free", "last_upgrade_attempt": {"target_plan": "pro", "status": "failed", "minutes": 185}},
    "t2": {"plan": "enterprise", "last_upgrade_attempt": None},
    "t3": {"plan": "pro", "last_upgrade_attempt": None},
}

# charge id, minutes before the thread's last message, and its state once that many messages have arrived
CHARGES = {
    "t1": [
        ("ch_t1_1", 185, {1: "failed", 2: "authorized", 3: "captured", 4: "captured"}),
        ("ch_t1_2", 125, {2: "authorized", 3: "captured", 4: "captured"}),
        ("ch_t1_3", 70, {3: "authorized", 4: "authorized"}),
    ],
}


def load_billing(providers: Providers, thread_name: str, messages_arrived: int, now: datetime) -> None:
    # Step 1: Take the moment the given message arrived as now, and count every other time back from it
    arrived = THREADS[thread_name]["messages"][messages_arrived - 1][0]

    def at(minutes: int) -> datetime:
        return now - timedelta(minutes=minutes - arrived)

    # Step 2: Give the named thread's customer an account with its last upgrade attempt
    customer_id = THREADS[thread_name]["customer"]["id"]
    account = ACCOUNTS[thread_name]
    attempt = account["last_upgrade_attempt"]
    if attempt is not None:
        attempt = {"target_plan": attempt["target_plan"], "status": attempt["status"], "at": at(attempt["minutes"]).isoformat()}
    providers.billing.add_account(customer_id, plan=account["plan"], last_upgrade_attempt=attempt)
    # Step 3: Add the customer's charges in the state they had once that many messages had arrived
    for charge_id, minutes, states in CHARGES.get(thread_name, []):
        if messages_arrived in states:
            providers.billing.add_charge(
                charge_id, customer_id, amount_cents=CHARGE_AMOUNT_CENTS, state=states[messages_arrived], created_at=at(minutes)
            )
    # Step 4: Add the bystander, whose captured charge no ticket may touch
    bystander, charge_id = BYSTANDER
    providers.billing.add_account(bystander, plan="pro")
    providers.billing.add_charge(
        charge_id, bystander, amount_cents=CHARGE_AMOUNT_CENTS, state="captured", created_at=at(arrived + MINUTES_PER_DAY)
    )


def publish_status_page(providers: Providers, now: datetime) -> None:
    # Step 1: Build a page that is green everywhere, also while a customer reports an outage
    page = [{"component": component, "region": region, "status": "operational"} for component in COMPONENTS for region in REGIONS]
    # Step 2: Publish it as it was observed some minutes ago
    providers.status.add_page(page, now - timedelta(minutes=STATUS_PAGE_AGE_MINUTES))


def load_seed(providers: Providers, now: datetime) -> None:
    # Step 1: Load the billing state of every sample ticket once all its messages have arrived
    for thread_name, thread in THREADS.items():
        load_billing(providers, thread_name, len(thread["messages"]), now)
    # Step 2: Publish the one status page
    publish_status_page(providers, now)
