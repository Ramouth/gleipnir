"""Who may do what — the agent roster, as an enforced table rather than prose.

The boundary this file draws already existed. It was written four times, in four
docstrings, and never in one place a test could read:

    `oracle.py`        "the only place a language model touches this system"
    `flags.py`         "the table is where the LLM's room to manoeuvre ends"
    `investigation.py` "an LLM may propose evidence ... it may not produce a boolean"
    `analyst.py`       "a green flag must never silently hide anything"

Four statements of one invariant, each enforced locally and none of them
enumerable. Nothing could answer *which parties act on this system, and what is
each one permitted to write* without a human reading four modules and trusting
that the fifth does not exist. This module is that answer, and it is executable:
:data:`ROLES` names every party, :class:`Capability` names every consequential
act, and :func:`check_invariants` fails if the two ever drift.

**A role is defined by what it may write, not by what it is made of.** Whether a
step runs as a regex, a rule table, a language model or a person is an
implementation fact. Whether its output can put a colour on a company is a
design fact, and it is the only one that matters here. So the roster is keyed on
capability, and :data:`MODEL_FORBIDDEN` is a property of the *table* — a new
model-backed role cannot be given the power to conclude without failing a test
that already exists.

**Deny-by-default, everywhere.** A role holds exactly the capabilities listed
against it. There is no inheritance, no ambient authority, and no capability
that defaults to granted. Adding one to a role is a visible diff on a table with
a stated reason, which is the only form of drift control that has survived
contact with this project.

**The forbidden list is not decoration.** Every role states, in
:attr:`Role.forbidden`, the capabilities it conspicuously lacks and why. A
reader's first question about the oracle is not "what may it do" — the schema
answers that — it is "why can it not just write the claim itself", and the
answer belongs next to the grant.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Autonomy(StrEnum):
    """How a role's output is produced. Bears on replay, not on authority.

    Kept separate from capability on purpose: `DETERMINISTIC` is not a licence
    and `MODEL` is not a demotion. The rule table concludes and is deterministic;
    the analyst concludes and is human; the oracle proposes and is a model. What
    each may write is stated once, in :attr:`Role.capabilities`, and read from
    there.
    """

    #: Pure code. Same inputs, same outputs, replayable from the raw store.
    DETERMINISTIC = "deterministic"
    #: A language model, bounded by a schema and verified after the fact.
    MODEL = "model"
    #: A person, attributable by name and dated.
    HUMAN = "human"


class Capability(StrEnum):
    """The consequential acts. If it cannot change what a reader sees or what
    the system spends, it is not in here."""

    #: Read the immutable raw store.
    READ_RAW = "read_raw"
    #: Append a payload to the raw store. Append-only; nothing overwrites.
    WRITE_RAW = "write_raw"
    #: Make an outbound request to a named source, against a rate-limited
    #: agreement. The scarce resource, and the one an agenda exists to ration.
    SPEND_QUOTA = "spend_quota"
    #: Admit a Claim into the graph — assert that a source says a thing.
    EMIT_CLAIM = "emit_claim"
    #: Produce a four-valued Result from claims.
    EVALUATE_PREDICATE = "evaluate_predicate"
    #: Offer a quote-backed answer to an approved question. Not a claim: an
    #: extractor still has to admit it.
    PROPOSE_EVIDENCE = "propose_evidence"
    #: Create a Finding — a coloured statement about a named party.
    RAISE_FINDING = "raise_finding"
    #: Decide the colour a reader sees.
    ASSIGN_COLOUR = "assign_colour"
    #: Stop a fired predicate reaching the report.
    SUPPRESS = "suppress"
    #: Resolve a review packet into one of the four permitted outcomes.
    RESOLVE_REVIEW = "resolve_review"


#: No model-backed role may hold any of these, ever.
#:
#: Each entry is a lesson rather than a preference. `EMIT_CLAIM`: a claim is an
#: assertion about what a source says, and a model that writes one directly has
#: no verifier between it and the graph — `oracle.verify_quotes` is that
#: verifier and it runs on a proposal. `EVALUATE_PREDICATE`, `RAISE_FINDING`,
#: `ASSIGN_COLOUR`: six iterations of measurement produced the four-valued
#: apparatus, the measured comparators and the closed `RedGround` set, and a
#: model permitted to conclude makes all three decorative. `SPEND_QUOTA`: the
#: engine decides what to look up (`plan.py`) — a model that could fetch would
#: turn a derived agenda back into an unbounded search. `SUPPRESS` and
#: `RESOLVE_REVIEW`: both are judgement, and judgement here is attributable to
#: a person by name or it is not admitted.
MODEL_FORBIDDEN: frozenset[Capability] = frozenset({
    Capability.EMIT_CLAIM,
    Capability.EVALUATE_PREDICATE,
    Capability.RAISE_FINDING,
    Capability.ASSIGN_COLOUR,
    Capability.SPEND_QUOTA,
    Capability.SUPPRESS,
    Capability.RESOLVE_REVIEW,
})


class CapabilityError(PermissionError):
    """A role attempted something outside its grant."""


@dataclass(frozen=True)
class Role:
    """One party acting on the system, and the exact extent of its authority."""

    name: str
    autonomy: Autonomy
    #: Where it lives. A role with no module is a design hole, not a plan.
    module: str
    #: One sentence: what this role is for.
    mandate: str
    capabilities: frozenset[Capability]
    #: What it reads, in the reader's terms.
    consumes: str
    #: What it writes, in the reader's terms.
    produces: str
    #: When it stops. Every role halts on a stated condition, because "the model
    #: decided it had read enough" is the failure `plan.py` was built against.
    halts_on: str
    #: Capabilities conspicuously absent, each with the reason. The reason is
    #: the point: a grant without a stated denial invites the next call site to
    #: widen it.
    forbidden: tuple[tuple[Capability, str], ...] = ()

    def may(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def require(self, capability: Capability) -> None:
        """Guard a call site. Raises rather than degrading quietly."""
        if not self.may(capability):
            raise CapabilityError(
                f"role {self.name!r} may not {capability}; "
                f"it holds {sorted(c.value for c in self.capabilities)}")


ROLES: dict[str, Role] = {r.name: r for r in (

    # ── deciding what to acquire ─────────────────────────────────────────────
    Role(
        name="planner",
        autonomy=Autonomy.DETERMINISTIC,
        module="gleipnir.plan, gleipnir.goals",
        mandate="Derive the ordered agenda: which unmet need, answerable by "
                "which source, could still flip an open goal.",
        capabilities=frozenset({Capability.READ_RAW}),
        consumes="goals, and the state of what is already held",
        produces="an ordered list of Needs, cheapest first, each carrying the "
                 "goal that wants it and why",
        halts_on="every remaining need is satisfied, unknowable, or cannot flip "
                 "a goal — computed, not guessed",
        forbidden=(
            (Capability.SPEND_QUOTA,
             "deciding and spending are separated so the agenda is auditable "
             "before a single request is made against the CVR agreement"),
            (Capability.EMIT_CLAIM,
             "the planner reasons about absence; it never asserts content"),
        ),
    ),

    # ── getting it ───────────────────────────────────────────────────────────
    Role(
        name="acquirer",
        autonomy=Autonomy.DETERMINISTIC,
        module="gleipnir.adapters.*, gleipnir.rawstore",
        mandate="Execute one Need against the one source named on it, within "
                "budget, and write what came back byte-for-byte.",
        capabilities=frozenset({Capability.READ_RAW, Capability.WRITE_RAW,
                                Capability.SPEND_QUOTA}),
        consumes="one Need",
        produces="a content-hashed blob plus an append-only fetch record — "
                 "including for a 404, which is evidence",
        halts_on="the need's budget is spent, or the source answered",
        forbidden=(
            (Capability.EMIT_CLAIM,
             "parsing is a separate pass, which is what makes a parser fix a "
             "reparse rather than a refetch against rate-limited quota"),
        ),
    ),

    # ── turning payloads into assertions ─────────────────────────────────────
    Role(
        name="extractor",
        autonomy=Autonomy.DETERMINISTIC,
        module="gleipnir.extract.*",
        mandate="Project one stored payload into claims, as a pure function, "
                "dropping no field silently.",
        capabilities=frozenset({Capability.READ_RAW, Capability.EMIT_CLAIM}),
        consumes="one raw payload, addressed by content hash",
        produces="claims, each carrying its raw_ref, its tier and its validity "
                 "period",
        halts_on="the payload is exhausted; extract/contract.py fails the run "
                 "if any field was dropped without a decision",
        forbidden=(
            (Capability.SPEND_QUOTA,
             "a pure function of stored bytes stays replayable; one that could "
             "fetch would make a reparse non-deterministic"),
            (Capability.RAISE_FINDING,
             "what a source says and what it means are different questions, "
             "and only the second one is allowed to colour anything"),
        ),
    ),

    # ── the one place a model reads prose ────────────────────────────────────
    Role(
        name="oracle",
        autonomy=Autonomy.MODEL,
        module="gleipnir.oracle",
        mandate="Transcribe named entities out of one bounded span of official "
                "prose into a closed schema, with a verbatim quote for each.",
        capabilities=frozenset({Capability.PROPOSE_EVIDENCE}),
        consumes="exactly one span, under 6,000 characters, and nothing else — "
                 "no other entity, no accumulated context",
        produces="typed entity references, each dropped in code unless its "
                 "quote appears verbatim in the span it came from",
        halts_on="one span, one call, one answer — cached on "
                 "sha256(question ‖ inputs ‖ prompt_version)",
        forbidden=(
            (Capability.EMIT_CLAIM,
             "`to_claims` is the extractor's admission step, downstream of "
             "`verify_quotes`; a model writing straight to the graph has no "
             "verifier between it and the reader"),
            (Capability.EVALUATE_PREDICATE,
             "it is never asked whether something is suspicious, to combine "
             "evidence, to rank, or to resolve a contradiction"),
            (Capability.SPEND_QUOTA,
             "the engine decides what to look up; the model answers what it is "
             "handed"),
        ),
    ),

    Role(
        name="investigator",
        autonomy=Autonomy.MODEL,
        module="gleipnir.investigation",
        mandate="Propose a quote-backed answer to one approved, data-answerable "
                "QuestionContract, while a ScreenMandate covering its source "
                "and subject kind is active.",
        capabilities=frozenset({Capability.READ_RAW,
                                Capability.PROPOSE_EVIDENCE}),
        consumes="a versioned question contract, the claims that triggered it, "
                 "and the one source the contract names",
        produces="an EvidenceProposal whose quote is verified against the "
                 "source text before anything else may read it",
        halts_on="the contract's required fields are filled, or the named "
                 "source cannot fill them — a question no connected source can "
                 "answer is not admitted in the first place",
        forbidden=(
            (Capability.EMIT_CLAIM,
             "only a source-specific extractor may turn a verified proposal "
             "into a claim"),
            (Capability.SPEND_QUOTA,
             "deny-by-default: a contract executes only against the single "
             "source its mandate permits, which is what keeps this narrower "
             "than a web-research agent"),
            (Capability.RESOLVE_REVIEW,
             "a passage needing interpretation becomes a ReviewPacket for a "
             "person; the packet deliberately holds no label for anyone's "
             "religion, politics, beliefs or intent"),
        ),
    ),

    # ── judging what the claims amount to ────────────────────────────────────
    Role(
        name="evaluator",
        autonomy=Autonomy.DETERMINISTIC,
        module="gleipnir.predicates.*, gleipnir.calibration",
        mandate="Evaluate each predicate four-valued against the claims, and "
                "attach the measured denominator.",
        capabilities=frozenset({Capability.READ_RAW,
                                Capability.EVALUATE_PREDICATE}),
        consumes="claims at an as-of date, plus the stratified base rates",
        produces="Results carrying the raw thresholded quantity, so a threshold "
                 "stays recalibratable and a finding stays defensible",
        halts_on="every predicate has a value; UNKNOWABLE is an answer, and it "
                 "is not the same answer as FALSE",
        forbidden=(
            (Capability.RAISE_FINDING,
             "a fired boolean is a fact with a denominator. Which facts may "
             "colour anything is a closed table, decided once, in flags.py"),
        ),
    ),

    Role(
        name="rule",
        autonomy=Autonomy.DETERMINISTIC,
        module="gleipnir.flags, gleipnir.finding",
        mandate="Sort fired booleans into RAISE, ESCALATE or FACT against a "
                "closed table, and assign the screen's colour.",
        capabilities=frozenset({Capability.EVALUATE_PREDICATE,
                                Capability.RAISE_FINDING,
                                Capability.ASSIGN_COLOUR}),
        consumes="Results",
        produces="findings, escalation questions naming their source, and rated "
                 "facts that may never become a colour",
        halts_on="the table is total: a fired boolean with no row is returned "
                 "as UNGOVERNED rather than dropped",
        forbidden=(
            (Capability.SPEND_QUOTA,
             "an ESCALATE row names the source and the question; acting on it "
             "is the acquirer's job and costs budget the planner rations"),
            (Capability.PROPOSE_EVIDENCE,
             "the rule layer assigns dispositions to evidence it is given; it "
             "does not go and find any"),
        ),
    ),

    # ── the people ───────────────────────────────────────────────────────────
    Role(
        name="analyst",
        autonomy=Autonomy.HUMAN,
        module="gleipnir.analyst",
        mandate="Record an attributable, dated judgement about one company, "
                "scoped to the named predicates it is allowed to suppress and "
                "pinned to the documents it was made against.",
        capabilities=frozenset({Capability.READ_RAW, Capability.WRITE_RAW,
                                Capability.SUPPRESS}),
        consumes="the screen, and public material no automated source can reach",
        produces="an append-only Observation; a withdrawal is a new record, "
                 "never a deletion",
        halts_on="the pinned documents move — the flag then goes stale, is "
                 "reported, and is not applied",
        forbidden=(
            (Capability.RAISE_FINDING,
             "the analyst's positive judgement suppresses within a stated "
             "scope; manufacturing a coloured finding by hand would bypass the "
             "RedGround requirement for an authority and a citation"),
            (Capability.EMIT_CLAIM,
             "the observation becomes a claim through `to_claim`, at the "
             "ANALYST tier, so its provenance is never mistaken for a "
             "registry's"),
        ),
    ),

    Role(
        name="reviewer",
        autonomy=Autonomy.HUMAN,
        module="gleipnir.investigation (ReviewPacket)",
        mandate="Read one bounded, already-retrieved passage and return one of "
                "four outcomes: irrelevant, identity unresolved, official "
                "security fact, escalate legal.",
        capabilities=frozenset({Capability.READ_RAW, Capability.WRITE_RAW,
                                Capability.RESOLVE_REVIEW}),
        consumes="a ReviewPacket: an attributable quote, its bounded context, "
                 "the retrieval reason, the source hash and the mandate",
        produces="a new append-only record; the original packet is never edited",
        halts_on="the packet is reviewed once and becomes immutable",
        forbidden=(
            (Capability.EMIT_CLAIM,
             "a review outcome creates no claim and no finding. None of the "
             "four outcomes is an assertion about a person's beliefs, and that "
             "is what keeps this queue lawful"),
            (Capability.ASSIGN_COLOUR,
             "the outcome routes the packet; it does not colour the company"),
        ),
    ),
)}

#: Reading order — how a fact travels from a source to a reader. The roster is a
#: dict for lookup; this is the order a human should read it in, and the order
#: the workbench renders.
PIPELINE: tuple[str, ...] = (
    "planner", "acquirer", "extractor", "oracle", "investigator",
    "evaluator", "rule", "analyst", "reviewer",
)


def role(name: str) -> Role:
    try:
        return ROLES[name]
    except KeyError:
        raise CapabilityError(f"no such role: {name!r}") from None


def require(name: str, capability: Capability) -> None:
    """Guard a call site by role name. See :meth:`Role.require`."""
    role(name).require(capability)


def holders(capability: Capability) -> tuple[str, ...]:
    """Every role holding a capability. The question an auditor actually asks:
    *who can colour a company?* — one name, and it is not a model."""
    return tuple(n for n in PIPELINE if ROLES[n].may(capability))


def check_invariants() -> None:
    """Fail loudly if the table has drifted. Called by the tests, and by the
    workbench on startup so a bad edit cannot be served to a reader."""
    if set(PIPELINE) != set(ROLES):
        raise CapabilityError("PIPELINE and ROLES disagree about which roles exist")

    for name, r in ROLES.items():
        if not (r.mandate and r.module and r.consumes and r.produces and r.halts_on):
            raise CapabilityError(f"role {name!r} has a required blank field")
        if not r.capabilities:
            raise CapabilityError(f"role {name!r} holds nothing — delete it or grant it")
        for cap, why in r.forbidden:
            if r.may(cap):
                raise CapabilityError(
                    f"role {name!r} both holds and forbids {cap}")
            if not why:
                raise CapabilityError(
                    f"role {name!r} forbids {cap} without saying why")

    for name, r in ROLES.items():
        if r.autonomy is not Autonomy.MODEL:
            continue
        overreach = r.capabilities & MODEL_FORBIDDEN
        if overreach:
            raise CapabilityError(
                f"model-backed role {name!r} holds {sorted(c.value for c in overreach)}; "
                "a model may propose evidence and nothing else")

    # The two questions a reader of a report is entitled to ask, answered by
    # construction rather than by inspection.
    if holders(Capability.ASSIGN_COLOUR) != ("rule",):
        raise CapabilityError("exactly one role may assign colour, and it is the rule table")
    if holders(Capability.RAISE_FINDING) != ("rule",):
        raise CapabilityError("exactly one role may raise a finding, and it is the rule table")

    orphans = [c for c in Capability if not holders(c)]
    if orphans:
        raise CapabilityError(
            f"capabilities nobody holds: {[c.value for c in orphans]} — "
            "an unheld capability is a design hole or a dead enum member")


def render() -> str:
    """The roster as text, for the CLI. The workbench renders the same table."""
    out: list[str] = []
    for name in PIPELINE:
        r = ROLES[name]
        out.append(f"{r.name.upper():<14}{r.autonomy:<15}{r.module}")
        out.append(f"{'':<14}{r.mandate}")
        out.append(f"{'':<14}may      {', '.join(sorted(c.value for c in r.capabilities))}")
        for cap, why in r.forbidden:
            out.append(f"{'':<14}may not  {cap.value} — {why}")
        out.append(f"{'':<14}halts    {r.halts_on}")
        out.append("")
    return "\n".join(out)
