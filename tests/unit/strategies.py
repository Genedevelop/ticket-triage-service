# generators of the records the domain reads, built from the real types: drafts, steps, actions and incidents
from string import ascii_lowercase

from hypothesis import strategies as st

from backend.application.tools.registry import REGISTRY, citable_tool_names, read_tool_names
from backend.domain import action_status
from backend.domain.draft import Draft, EvidenceConflict
from backend.domain.policy import POLICY

# ids from a small alphabet, so a cited id and a returned id often meet
ARTICLE_IDS = st.text(alphabet="ab", min_size=1, max_size=2)
# a reply the model left out, wrote, or filled with blanks only
REPLY_DRAFTS = st.none() | st.text(max_size=20) | st.text(alphabet=" \t\n", max_size=3)
# a reply with something to read in it three times in four
REPLIES_MOSTLY_WRITTEN = st.one_of(*[st.text(min_size=1, max_size=20).filter(str.strip)] * 3, REPLY_DRAFTS)
# free text as a model may write it: half the time with a blank at each end, which the decision must keep
FREE_TEXT = st.text() | st.text().map(lambda text: f" {text} ")
# the tools whose calls the decision reads most closely come up most, beside every registered tool and names no tool has
TOOL_NAMES = st.one_of(
    st.sampled_from(sorted(citable_tool_names())),
    st.sampled_from(sorted(read_tool_names())),
    st.sampled_from(sorted(REGISTRY)),
    st.text(alphabet=ascii_lowercase + "_", min_size=1, max_size=12),
)
STATUSES = st.sampled_from(sorted(action_status.STATUSES))


# a model of the given class with every field drawn from its annotation, unless a strategy is given for it
def models(model, **given):
    drawn = {name: st.from_type(field.annotation) for name, field in model.model_fields.items() if name not in given}
    return st.builds(model, **drawn, **given)


ARTICLES = st.lists(st.fixed_dictionaries({"id": ARTICLE_IDS}, optional={"title": st.text(max_size=10)}), max_size=3)
ERRORS = st.fixed_dictionaries({"type": st.text(max_size=8)})
# what a step stored: articles, an error, both or neither, whatever the step's outcome says
RESULTS = st.fixed_dictionaries({"articles": ARTICLES}, optional={"error": ERRORS}) | st.fixed_dictionaries({}, optional={"error": ERRORS})
STEP = st.fixed_dictionaries(
    {
        "kind": st.sampled_from(["tool", "tool", "tool", "model"]),
        "name": TOOL_NAMES,
        "arguments": st.just({}),
        "result": RESULTS,
        "ok": st.booleans(),
        "duration_ms": st.integers(min_value=0, max_value=60_000),
    }
)


# the steps of one turn: any steps, then some of them taken again with any outcome, as a model that retries a call does
@st.composite
def turn_steps(draw):
    first = draw(st.lists(STEP, max_size=4))
    if not first:
        return first
    retried = draw(st.lists(st.sampled_from(first), max_size=3))
    return first + [{**step, "ok": draw(st.booleans()), "result": draw(RESULTS)} for step in retried]


STEPS = turn_steps()


# a draft citing any articles, drawn mostly from the ids the given steps returned when they returned any
def drafts_citing(steps, **given):
    returned = sorted({article["id"] for step in steps for article in step["result"].get("articles", [])})
    cited = st.sampled_from(returned) | ARTICLE_IDS if returned else ARTICLE_IDS
    fields = {
        "evidence_conflicts": st.lists(models(EvidenceConflict), max_size=2),
        "kb_article_ids": st.lists(cited, max_size=4),
        "customer_reply_draft": REPLY_DRAFTS,
        "rationale": FREE_TEXT,
        "language": FREE_TEXT,
        "operator_reply": st.none() | FREE_TEXT,
        **given,
    }
    return models(Draft, **fields)


# a turn as the decision sees it: the draft the model wrote, after the steps it took
def turns(**given):
    return STEPS.flatmap(lambda steps: st.tuples(drafts_citing(steps, **given), st.just(steps)))


TURNS = turns()
# the turns the rules weigh most: the model wants its answer sent with nobody reading it first
TURNS_ANSWERED_UNATTENDED = turns(next_action=st.just("auto_respond"))
DRAFTS = drafts_citing([])


# a turn whose answer may go out as drafted: the last search returned the article cited, so a person is needed for other causes or none
@st.composite
def turns_citing_an_article_the_last_search_returned(draw):
    article_id = draw(ARTICLE_IDS)
    search = {
        **draw(STEP),
        "kind": "tool",
        "name": draw(st.sampled_from(sorted(citable_tool_names()))),
        "result": {"articles": [{"id": article_id}]},
        "ok": True,
    }
    draft = draw(
        drafts_citing(
            [],
            next_action=st.just("auto_respond"),
            kb_article_ids=st.just([article_id]),
            evidence_conflicts=st.just([]) | st.lists(models(EvidenceConflict), min_size=1, max_size=2),
            customer_reply_draft=REPLIES_MOSTLY_WRITTEN,
        )
    )
    return draft, draw(STEPS) + [search]


TURNS_ANSWERED_UNATTENDED_FROM_A_RETURNED_ARTICLE = turns_citing_an_article_the_last_search_returned()

# ids one search may return together: the six any step may return and two no other step does
ARTICLE_IDS_OF_ONE_SEARCH = ["a", "b", "aa", "ab", "ba", "bb", "c", "cc"]
# ids no step of a generated turn returns: their alphabet is not the one the steps draw from
ARTICLE_IDS_NEVER_RETURNED = st.text(alphabet="xy", min_size=1, max_size=2)


# a turn where cited and returned ids always meet: one successful search, one failed, a draft citing both and ids never returned
@st.composite
def turns_citing_returned_and_unreturned_articles(draw):
    names = st.sampled_from(sorted(citable_tool_names()))
    ids = draw(st.permutations(ARTICLE_IDS_OF_ONE_SEARCH))[: draw(st.integers(min_value=3, max_value=5))]
    refused = ids[:1]
    returned = ids[1:]
    search = {**draw(STEP), "kind": "tool", "name": draw(names), "result": {"articles": [{"id": i} for i in returned]}, "ok": True}
    failed = {**draw(STEP), "kind": "tool", "name": draw(names), "result": {"articles": [{"id": i} for i in refused]}, "ok": False}
    cited_and_returned = draw(st.lists(st.sampled_from(returned), min_size=1, max_size=len(returned), unique=True))
    repeated = draw(st.sampled_from(cited_and_returned))
    never_returned = draw(st.lists(ARTICLE_IDS_NEVER_RETURNED, min_size=1, max_size=2))
    cited = draw(st.permutations(cited_and_returned + [repeated] + never_returned + refused))
    draft = draw(drafts_citing([], kb_article_ids=st.just(list(cited))))
    return draft, draw(st.permutations(draw(STEPS) + [search, failed])), never_returned


TURNS_CITING_RETURNED_AND_UNRETURNED_ARTICLES = turns_citing_returned_and_unreturned_articles()

ACTIONS = st.lists(
    st.fixed_dictionaries({"id": st.uuids(), "kind": st.sampled_from(sorted(POLICY)) | st.text(max_size=8), "status": STATUSES}),
    max_size=3,
)

# an answer of the incident system, which says whether the incident was open already, and events that carry anything else
EVENT_DETAILS = st.none() | st.fixed_dictionaries({}, optional={"already_open": st.booleans(), "reason": st.text(max_size=8)})
EVENTS = st.lists(
    st.fixed_dictionaries({"type": st.just("provider_result"), "detail": st.fixed_dictionaries({"already_open": st.booleans()})})
    | st.fixed_dictionaries({"type": st.sampled_from(["proposed", "joined", "claimed", "provider_result"]), "detail": EVENT_DETAILS}),
    max_size=4,
)
INCIDENTS = st.lists(
    st.fixed_dictionaries({"id": st.uuids(), "kind": st.just("incident"), "status": STATUSES, "events": EVENTS}), max_size=2
)
