"""The analyst workbench — HTTP over the same library the CLI calls.

Server-rendered, one language, no build step. HTML is another projection of the
`Screen` and the `Sheet`, exactly as `screen.render` and `scripts/factsheet.py`
are, and it is a projection with no arithmetic of its own: a template here can
iterate, escape and format, and that is the entire list.

**Three refusals the routes enforce, because a UI is where they get lost.**

*The workbench never spends registry quota.* `budget` defaults to zero and no
route raises it. A page a click can drive must not be able to burn a
rate-limited system-to-system agreement, and "refresh this company" is exactly
the button that would.

*Colour comes from `Screen.colour` and nowhere else.* No template computes one,
no route adds one up, and the badge on the index is the register's own status
rather than a verdict.

*The two writing routes are the two human roles.* `agents.py` grants `SUPPRESS`
to the analyst and `RESOLVE_REVIEW` to the reviewer, and those are the only
POSTs that exist. Both append; neither edits.

Progressive enhancement throughout: htmx narrows the company list without a
round trip when it loads, and every control is a plain link or form that works
when it does not.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from gleipnir import agents
from gleipnir.config import settings
from gleipnir.factsheet import NotInStore
from gleipnir.finding import Colour
from gleipnir.investigation import InvestigationError, ReviewOutcome
from gleipnir.predicates.core import V
from gleipnir.rawstore import RawStore
from gleipnir.web.workbench import Workbench

HERE = Path(__file__).parent

#: How a four-valued result is worded to a reader. The value itself never
#: appears: `factsheet.SECTIONS` already decided that a boolean is scaffolding.
VALUE_WORDS: dict[V, str] = {
    V.TRUE: "established",
    V.FALSE: "checked, positively absent",
    V.UNKNOWN: "answerable by spending more",
    V.UNKNOWABLE: "no connected source covers it",
}

MAX_BLOB_CHARS = 200_000


def _as_of(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def create_app(store: RawStore | None = None, *, budget: int = 0) -> FastAPI:
    """Build the app. `store` is injectable so tests run against a temp store."""
    agents.check_invariants()      # a bad roster is never served to a reader

    bench = Workbench(store=store or RawStore(settings.raw_store_path), budget=budget)
    app = FastAPI(title="Gleipnir workbench", docs_url=None, redoc_url=None)
    app.state.bench = bench
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.globals.update(
        VALUE_WORDS=VALUE_WORDS, Colour=Colour, V=V,
        Capability=agents.Capability,
        agent_roles=[agents.ROLES[n] for n in agents.PIPELINE],
    )

    def page(request: Request, name: str, status_code: int = 200,
             **ctx: Any) -> HTMLResponse:
        return templates.TemplateResponse(request, name, ctx,
                                          status_code=status_code)

    # ── the index ────────────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, q: str = "") -> HTMLResponse:
        rows = _match(bench.companies(), q)
        return page(request, "index.html", rows=rows, q=q, stats=bench.stats(),
                    total=len(bench.companies()))

    @app.get("/partials/companies", response_class=HTMLResponse)
    def companies_partial(request: Request, q: str = "") -> HTMLResponse:
        return page(request, "_companies.html", rows=_match(bench.companies(), q),
                    total=len(bench.companies()), q=q)

    # ── one company ──────────────────────────────────────────────────────────
    @app.get("/c/{cvr}", response_class=HTMLResponse)
    def company(request: Request, cvr: str, as_of: str = "") -> HTMLResponse:
        try:
            sheet = bench.sheet(cvr, _as_of(as_of))
        except NotInStore:
            return page(request, "missing.html", status_code=404, cvr=cvr)
        screen = bench.screen(cvr, sheet.as_of)
        flag, flag_status = bench.flag(cvr)
        return page(
            request, "company.html", sheet=sheet, screen=screen, flag=flag,
            flag_status=flag_status, observations=bench.observations(cvr),
            as_of_param=as_of,
            # Only what actually fired can be suppressed. Offering the whole
            # predicate list would invite a flag that scopes over questions the
            # register never answered for this company.
            suppressable=sorted({r.predicate for r in sheet.results
                                 if r.value in (V.TRUE, V.UNKNOWN)}),
        )

    @app.post("/c/{cvr}/observation")
    def record(cvr: str, analyst: str = Form(...), verdict: str = Form(...),
               basis: str = Form(...), sources: str = Form(""),
               suppresses: list[str] = Form(default=[]),
               as_of: str = Form("")) -> RedirectResponse:
        """The analyst's one write. Appended, attributed, and pinned in the
        workbench to every document on file for this company right now."""
        when = _as_of(as_of) or datetime.now(timezone.utc).date()
        if verdict not in ("green", "amber", "withdraw"):
            return RedirectResponse(f"/c/{cvr}?error=verdict", status_code=303)
        if not (analyst.strip() and basis.strip()):
            return RedirectResponse(f"/c/{cvr}?error=attribution", status_code=303)
        bench.record_observation(
            cvr=cvr, verdict=verdict, basis=basis.strip(), who=analyst.strip(),
            as_of=when,
            sources=tuple(s.strip() for s in sources.split(",") if s.strip()),
            # A withdrawal scopes over nothing: it is the removal of a
            # judgement, not a differently-scoped one.
            suppresses=() if verdict == "withdraw" else tuple(suppresses))
        return RedirectResponse(f"/c/{cvr}#judgement", status_code=303)

    # ── provenance ───────────────────────────────────────────────────────────
    @app.get("/evidence/{content_hash}", response_class=HTMLResponse)
    def evidence(request: Request, content_hash: str) -> HTMLResponse:
        """The bytes behind a line, and every time we fetched them.

        Every fact on a company page links here. A citation a reader cannot
        open is a citation they have to take on trust, which is the thing this
        project spends its architecture avoiding.
        """
        fetches = [f for f in bench.store.fetches() if f.content_hash == content_hash]
        body, truncated, size = _blob(bench, content_hash)
        return page(request, "evidence.html", content_hash=content_hash,
                    fetches=sorted(fetches, key=lambda f: f.fetched_at, reverse=True),
                    body=body, truncated=truncated, size=size)

    # ── the queues ───────────────────────────────────────────────────────────
    @app.get("/queue", response_class=HTMLResponse)
    def queue(request: Request) -> HTMLResponse:
        packets = bench.review_packets()
        return page(request, "queue.html",
                    pending=[(h, p) for h, p in packets
                             if p.outcome is ReviewOutcome.PENDING],
                    closed=[(h, p) for h, p in packets
                            if p.outcome is not ReviewOutcome.PENDING],
                    outcomes=[o for o in ReviewOutcome if o is not ReviewOutcome.PENDING])

    @app.post("/queue/{content_hash}")
    def resolve(content_hash: str, outcome: str = Form(...),
                reviewer: str = Form(...), rationale: str = Form(...)):
        """The reviewer's one write. Four outcomes, none of them a label about
        anybody's beliefs, and none of them creating a claim."""
        try:
            bench.resolve_review(content_hash, outcome=ReviewOutcome(outcome),
                                 reviewer=reviewer.strip(), rationale=rationale.strip())
        except (ValueError, InvestigationError) as exc:
            return RedirectResponse(f"/queue?error={type(exc).__name__}", status_code=303)
        return RedirectResponse("/queue", status_code=303)

    # ── who may do what ──────────────────────────────────────────────────────
    @app.get("/agents", response_class=HTMLResponse)
    def roster(request: Request) -> HTMLResponse:
        return page(request, "agents.html",
                    capabilities=list(agents.Capability),
                    holders={c: agents.holders(c) for c in agents.Capability},
                    model_forbidden=sorted(c.value for c in agents.MODEL_FORBIDDEN))

    @app.get("/health")
    def health() -> dict[str, Any]:
        agents.check_invariants()
        return {"ok": True, "companies": len(bench.companies()),
                "budget": bench.budget}

    return app


def _match(rows, q: str):
    """Filter the index. Substring over CVR and name — no ranking, because a
    ranked list of companies is a judgement about which to open first."""
    q = (q or "").strip().casefold()
    if not q:
        return rows
    return [r for r in rows if q in r.cvr or q in (r.name or "").casefold()]


def _blob(bench: Workbench, content_hash: str) -> tuple[str, bool, int]:
    """A stored payload as text, truncated for display only.

    The truncation is stated on the page. A viewer that silently showed the
    first page of a document would be a worse citation than no viewer.
    """
    path = bench.store.path_of(content_hash)
    if not path.exists():
        return "", False, 0
    size = path.stat().st_size
    raw = path.read_bytes()[: MAX_BLOB_CHARS + 1]
    text = raw.decode("utf-8", errors="replace")
    truncated = size > MAX_BLOB_CHARS
    if not truncated:
        try:
            text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    return text[:MAX_BLOB_CHARS], truncated, size
