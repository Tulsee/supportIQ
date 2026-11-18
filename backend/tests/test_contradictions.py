"""Knowledge-base contradiction scanning (Module 13) end to end, with a scripted model,
the hashing embedder and in-memory Qdrant: scans on demand, findings with verified
quotes, pairs judged once, failures retried, reviews in the audit log, findings
resolved when a document changes, scheduled scans, and staff-only data."""

import json
import uuid
from typing import Any

import httpx2
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.generation.llm import LLMError, Prompt
from ai_engine.retrieval.vector_store import QdrantVectorStore
from app.models import ContradictionCheck, ContradictionScan
from app.services.contradictions import ContradictionScanner, run_scan, schedule_due_scans
from app.workers.queue import QueueUnavailableError
from tests.factories import (
    API,
    RecordingContradictionQueue,
    ScriptedLLM,
    Workspace,
    create_org,
    in_org,
)
from tests.test_retrieval import upload_and_index

pytestmark = pytest.mark.anyio


async def set_rls_context_bypass(session: AsyncSession) -> None:
    from app.core.database import set_rls_context

    await set_rls_context(session, bypass=True)


POLICY = b"""# Returns policy

## Returning a product

You can return an unopened Quillon router within 30 days of delivery for a full refund.
Opened routers can be returned within 14 days if they are faulty.
"""
FAQ = b"""# Help center FAQ

## Can I return my Quillon router?

Yes. You can return any Quillon router within 60 days for a full refund, no questions asked.
"""
FAQ_FIXED = b"""# Help center FAQ

## Can I return my Quillon router?

Yes. You can return an unopened Quillon router within 30 days of delivery for a full refund.
"""
WARRANTY = b"""# Warranty

## Coverage

Quillon routers have a two year limited warranty against defects in materials.
"""

CONFLICT = {
    "topic": "return window",
    "quote_a": "You can return an unopened Quillon router within 30 days of delivery",
    "quote_b": "You can return any Quillon router within 60 days for a full refund",
    "explanation": "The policy says 30 days for unopened routers; the FAQ says 60 days.",
}


def judge_reply(prompt: Prompt) -> str:
    """Conflict when the pair is the policy and the old FAQ; consistent otherwise."""
    if "60 days" in prompt.user and "30 days" in prompt.user:
        return json.dumps({"verdict": "conflict", "confidence": "high", "conflicts": [CONFLICT]})
    return json.dumps({"verdict": "consistent", "confidence": "high", "conflicts": []})


@pytest.fixture(autouse=True)
def scan_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Small test documents: every passage counts and any shared words pair them."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "contradiction_min_tokens", 5)
    monkeypatch.setattr(settings, "contradiction_min_similarity", 0.0)
    monkeypatch.setattr(settings, "contradiction_neighbours", 5)


@pytest.fixture
def scripted(llm: ScriptedLLM) -> ScriptedLLM:
    llm.reply = judge_reply
    return llm


@pytest.fixture
async def documents(api: httpx2.AsyncClient, ws: Workspace, ingest) -> dict[str, str]:
    return {
        name: await upload_and_index(api, ws.admin, ws.org_id, ingest, f"{name}.md", body)
        for name, body in (("policy", POLICY), ("faq", FAQ), ("warranty", WARRANTY))
    }


async def request_scan(api: httpx2.AsyncClient, ws: Workspace, expect: int = 202) -> dict:
    response = await api.post(
        f"{API}/knowledge/contradictions/scans", headers=in_org(ws.admin, ws.org_id)
    )
    assert response.status_code == expect, response.text
    return response.json()


async def run(
    db_session: AsyncSession, queue: RecordingContradictionQueue, llm, vector_store
) -> ContradictionScan:
    """Run the last queued scan in-process, as a worker would."""
    scan = await run_scan(db_session, queue.ids[-1], llm=llm, vectors=vector_store)
    assert scan is not None
    return scan


async def scan_now(api, ws, db_session, contradiction_queue, llm, vector_store) -> dict:
    await request_scan(api, ws)
    await run(db_session, contradiction_queue, llm, vector_store)
    return await summary(api, ws)


async def summary(api: httpx2.AsyncClient, ws: Workspace) -> dict[str, Any]:
    response = await api.get(
        f"{API}/knowledge/contradictions/summary", headers=in_org(ws.admin, ws.org_id)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def findings(api: httpx2.AsyncClient, ws: Workspace, **params: Any) -> list[dict]:
    response = await api.get(
        f"{API}/knowledge/contradictions", params=params, headers=in_org(ws.agent, ws.org_id)
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def test_a_scan_finds_the_conflict_with_quotes_from_both_documents(
    api, ws, documents, db_session, contradiction_queue, scripted, vector_store
) -> None:
    scan = await request_scan(api, ws)
    assert scan["status"] == "running" and scan["trigger"] == "manual"
    assert contradiction_queue.ids == [uuid.UUID(scan["id"])]
    await run(db_session, contradiction_queue, scripted, vector_store)

    state = await summary(api, ws)
    latest = state["latest_scan"]
    assert latest["status"] == "finished" and latest["findings"] == 1 and latest["failed"] == 0
    assert latest["checked"] == latest["candidates"] >= 2  # every cross-document pair
    assert state["open"] == 1 and state["pairs_checked"] == latest["checked"]

    [finding] = await findings(api, ws, status="open")
    titles = {finding["a"]["document_title"], finding["b"]["document_title"]}
    assert titles == {"Policy", "Faq"}  # titles from the file names
    [conflict] = finding["conflicts"]
    assert conflict["grounded"] and conflict["topic"] == "return window"
    # Each quote is reported against the passage it came from.
    side = {finding["a"]["document_id"]: "a", finding["b"]["document_id"]: "b"}
    assert conflict[f"quote_{side[documents['policy']]}"].startswith("You can return an unopened")
    assert finding["a"]["current"] and finding["b"]["current"]
    # Only documents' text goes to the model, fenced as data.
    assert all('<passage id="A">' in p.user for p in scripted.prompts)

    # Filter by document.
    assert len(await findings(api, ws, document_id=documents["faq"])) == 1
    assert await findings(api, ws, document_id=documents["warranty"]) == []


async def test_pairs_are_judged_once_and_again_only_when_their_text_changes(
    api, ws, documents, db_session, contradiction_queue, scripted, vector_store, ingest
) -> None:
    await scan_now(api, ws, db_session, contradiction_queue, scripted, vector_store)
    asked = len(scripted.prompts)
    state = await scan_now(api, ws, db_session, contradiction_queue, scripted, vector_store)
    assert len(scripted.prompts) == asked  # nothing new to judge
    assert state["latest_scan"]["checked"] == 0 and state["open"] == 1

    # The FAQ is corrected: the old finding resolves itself, the new text is checked.
    response = await api.post(
        f"{API}/documents/{documents['faq']}/versions",
        files={"file": ("faq.md", FAQ_FIXED, "text/markdown")},
        headers=in_org(ws.admin, ws.org_id),
    )
    assert response.status_code == 202, response.text
    await ingest(documents["faq"])
    state = await scan_now(api, ws, db_session, contradiction_queue, scripted, vector_store)
    assert state["latest_scan"]["auto_resolved"] == 1
    assert state["latest_scan"]["checked"] >= 1 and state["latest_scan"]["findings"] == 0
    assert state["open"] == 0 and state["resolved"] == 1
    [resolved] = await findings(api, ws, status="resolved")
    assert resolved["status_reason"] == "document_changed"
    # The copy still shows what conflicted.
    faq_side = "a" if resolved["a"]["document_id"] == documents["faq"] else "b"
    assert "60 days" in resolved[faq_side]["content"] and not resolved[faq_side]["current"]


async def test_a_conflict_the_model_cannot_quote_is_not_reported(
    api, ws, documents, db_session, contradiction_queue, scripted, vector_store
) -> None:
    invented = {**CONFLICT, "quote_b": "Routers can be returned within 90 days of purchase."}
    scripted.reply = lambda prompt: json.dumps(
        {
            "verdict": "conflict",
            "confidence": "high",
            "conflicts": [invented if "60 days" in prompt.user else CONFLICT],
        }
    )
    state = await scan_now(api, ws, db_session, contradiction_queue, scripted, vector_store)
    assert state["open"] == 0
    await set_rls_context_bypass(db_session)
    rows = (await db_session.scalars(select(ContradictionCheck))).all()
    assert rows and all(not r.flagged and r.status is None for r in rows)
    # The judgement is kept (with which quote was missing), so it isn't asked again.
    assert any(
        r.verdict == "conflict" and r.conflicts and r.conflicts[0]["found_b"] is False for r in rows
    )


async def test_low_confidence_is_not_reported(
    api, ws, documents, db_session, contradiction_queue, scripted, vector_store
) -> None:
    scripted.reply = lambda prompt: judge_reply(prompt).replace('"high"', '"low"')
    state = await scan_now(api, ws, db_session, contradiction_queue, scripted, vector_store)
    assert state["open"] == 0


async def test_model_failures_are_counted_and_retried_by_the_next_scan(
    api, ws, documents, db_session, contradiction_queue, scripted, vector_store
) -> None:
    scripted.error = LLMError("The model isn't running")
    state = await scan_now(api, ws, db_session, contradiction_queue, scripted, vector_store)
    latest = state["latest_scan"]
    assert latest["status"] == "finished" and latest["failed"] == latest["checked"] > 0
    assert state["pairs_checked"] == 0 and state["open"] == 0

    scripted.error = None
    state = await scan_now(api, ws, db_session, contradiction_queue, scripted, vector_store)
    assert state["latest_scan"]["checked"] == latest["checked"] and state["open"] == 1
    await set_rls_context_bypass(db_session)
    rows = (await db_session.scalars(select(ContradictionCheck))).all()
    assert all(r.error is None and r.verdict for r in rows)  # the failures were replaced


async def test_an_unusable_reply_is_a_failure(
    api, ws, documents, db_session, contradiction_queue, scripted, vector_store
) -> None:
    scripted.reply = "They look different to me."
    state = await scan_now(api, ws, db_session, contradiction_queue, scripted, vector_store)
    assert state["latest_scan"]["failed"] == state["latest_scan"]["checked"] > 0
    assert state["pairs_checked"] == 0


async def test_a_scan_stops_at_the_per_scan_limit_and_the_next_continues(
    api, ws, documents, db_session, contradiction_queue, scripted, vector_store, monkeypatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "contradiction_max_pairs_per_scan", 1)
    first = (await scan_now(api, ws, db_session, contradiction_queue, scripted, vector_store))[
        "latest_scan"
    ]
    assert first["checked"] == 1 and first["remaining"] == first["candidates"] - 1
    second = (await scan_now(api, ws, db_session, contradiction_queue, scripted, vector_store))[
        "latest_scan"
    ]
    assert second["checked"] == 1 and second["remaining"] == first["remaining"] - 1


async def test_only_one_scan_runs_at_a_time(
    api, ws, documents, db_session, contradiction_queue, scripted, vector_store
) -> None:
    await request_scan(api, ws)
    error = await request_scan(api, ws, expect=409)
    assert error["error"]["code"] == "scan_running"
    await run(db_session, contradiction_queue, scripted, vector_store)
    await request_scan(api, ws)  # finished: a new one can start


async def test_an_unavailable_queue_fails_the_scan_cleanly(
    api, ws, documents, contradiction_queue
) -> None:
    contradiction_queue.error = QueueUnavailableError("redis down")
    await request_scan(api, ws, expect=503)
    state = await summary(api, ws)
    assert state["latest_scan"]["status"] == "failed"
    contradiction_queue.error = None
    await request_scan(api, ws)


async def test_reviews_are_admin_only_audited_and_dismissals_stick(
    api, ws, documents, db_session, contradiction_queue, scripted, vector_store
) -> None:
    await scan_now(api, ws, db_session, contradiction_queue, scripted, vector_store)
    [finding] = await findings(api, ws)
    url = f"{API}/knowledge/contradictions/{finding['id']}"

    response = await api.patch(
        url, json={"status": "dismissed"}, headers=in_org(ws.agent, ws.org_id)
    )
    assert response.status_code == 403
    response = await api.patch(
        url,
        json={"status": "dismissed", "note": "The FAQ is archived."},
        headers=in_org(ws.admin, ws.org_id),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "dismissed" and body["note"] == "The FAQ is archived."
    assert body["reviewed_by"]["full_name"] == "Ada Admin"

    audit = await api.get(
        f"{API}/audit-log",
        params={"entity_type": "contradiction"},
        headers=in_org(ws.admin, ws.org_id),
    )
    assert audit.status_code == 200, audit.text
    [entry] = audit.json()["items"]
    assert entry["action"] == "contradiction.dismissed" and "return window" in entry["summary"]

    # A dismissed finding isn't reported again while its text is unchanged.
    state = await scan_now(api, ws, db_session, contradiction_queue, scripted, vector_store)
    assert state["open"] == 0 and state["dismissed"] == 1

    reopened = await api.patch(url, json={"status": "open"}, headers=in_org(ws.admin, ws.org_id))
    assert reopened.status_code == 200 and reopened.json()["status"] == "open"
    assert reopened.json()["note"] is None


async def test_findings_are_staff_only_and_tenant_scoped(
    api, ws, documents, db_session, contradiction_queue, scripted, vector_store, outbox
) -> None:
    await scan_now(api, ws, db_session, contradiction_queue, scripted, vector_store)
    [finding] = await findings(api, ws)
    url = f"{API}/knowledge/contradictions"

    customer = in_org(ws.customer, ws.org_id)
    assert (await api.get(url, headers=customer)).status_code == 403
    assert (await api.get(f"{url}/summary", headers=customer)).status_code == 403
    agent = in_org(ws.agent, ws.org_id)
    assert (await api.post(f"{url}/scans", headers=agent)).status_code == 403
    assert (await api.get(f"{url}/{finding['id']}", headers=agent)).status_code == 200

    # Another organization's admin sees nothing, even by id.
    other = await create_org(api, ws.admin, "Other Co")
    elsewhere = in_org(ws.admin, other["id"])
    assert (await api.get(f"{url}/{finding['id']}", headers=elsewhere)).status_code == 404
    assert (await api.get(url, headers=elsewhere)).json()["total"] == 0

    # Row-level security: staff of this organization only.
    from app.core.database import set_rls_context

    async def visible(user, org_id: str) -> list[int]:
        await set_rls_context(
            db_session,
            organization_id=uuid.UUID(org_id),
            user_id=uuid.UUID(user.id),
            bypass=False,
        )
        return [
            (await db_session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
            for table in ("contradiction_checks", "contradiction_scans")
        ]

    checks, scans = await visible(ws.agent, ws.org_id)
    assert checks > 0 and scans == 1
    assert await visible(ws.customer, ws.org_id) == [0, 0]
    assert await visible(ws.admin, other["id"]) == [0, 0]


async def test_scheduled_scans_run_only_where_turned_on_and_not_too_often(
    api, ws, documents, db_session, contradiction_queue
) -> None:
    assert await schedule_due_scans(db_session, contradiction_queue) == 0

    response = await api.patch(
        f"{API}/organizations/{ws.org_id}",
        json={"settings": {"contradiction_scan": "on"}},
        headers=ws.admin.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["settings"]["contradiction_scan"] == "on"

    assert await schedule_due_scans(db_session, contradiction_queue) == 1
    scan = await db_session.get(ContradictionScan, contradiction_queue.ids[-1])
    assert scan is not None and scan.trigger == "scheduled"
    # Running, then recent: not started again.
    assert await schedule_due_scans(db_session, contradiction_queue) == 0
    assert (await summary(api, ws))["scanning_enabled"] is True


async def test_the_scanner_pairs_passages_across_documents_only(
    api, ws, documents, db_session, scripted, vector_store: QdrantVectorStore
) -> None:
    from app.core.database import set_rls_context

    await set_rls_context(db_session, bypass=True)
    candidates = await ContradictionScanner(db_session, scripted, vector_store).candidates(
        uuid.UUID(ws.org_id)
    )
    assert candidates
    assert all(c.a.document_id != c.b.document_id for c in candidates)
    keys = [c.pair.key for c in candidates]
    assert len(keys) == len(set(keys))


async def test_a_scheduled_first_pass_continues_while_pairs_are_left(
    api, ws, documents, db_session, contradiction_queue, scripted, vector_store, monkeypatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "contradiction_max_pairs_per_scan", 1)
    response = await api.patch(
        f"{API}/organizations/{ws.org_id}",
        json={"settings": {"contradiction_scan": "on"}},
        headers=ws.admin.headers,
    )
    assert response.status_code == 200, response.text

    assert await schedule_due_scans(db_session, contradiction_queue) == 1
    scan = await run(db_session, contradiction_queue, scripted, vector_store)
    assert scan.remaining > 0
    # Pairs left: the next check continues instead of waiting for the interval.
    assert await schedule_due_scans(db_session, contradiction_queue) == 1
    while (scan := await run(db_session, contradiction_queue, scripted, vector_store)).remaining:
        assert await schedule_due_scans(db_session, contradiction_queue) == 1
    # Done: nothing until the interval passes.
    assert await schedule_due_scans(db_session, contradiction_queue) == 0


async def test_a_conflict_seen_in_one_order_only_is_not_reported(
    api, ws, documents, db_session, contradiction_queue, scripted, vector_store
) -> None:
    asked: list[str] = []

    def one_order(prompt: Prompt) -> str:
        # The conflicting pair: a conflict the first time, unrelated when swapped.
        if "60 days" in prompt.user and "30 days" in prompt.user:
            asked.append(prompt.user)
            if len(asked) > 1:
                return json.dumps({"verdict": "unrelated", "confidence": "high", "conflicts": []})
        return judge_reply(prompt)

    scripted.reply = one_order
    state = await scan_now(api, ws, db_session, contradiction_queue, scripted, vector_store)
    assert state["open"] == 0
    assert len(asked) == 2 and asked[0] != asked[1]  # the same pair, swapped
    await set_rls_context_bypass(db_session)
    [row] = [
        r
        for r in (await db_session.scalars(select(ContradictionCheck))).all()
        if r.verdict == "conflict"
    ]
    # Both answers are kept; the second was asked only because the first flagged it.
    verdicts = [j["verdict"] if j else None for j in row.signals["judgements"]]
    assert sorted(verdicts) == ["conflict", "unrelated"] and not row.flagged
