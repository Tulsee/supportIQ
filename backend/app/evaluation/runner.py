"""Run labelled questions through the real pipeline.

The corpus is indexed into a dedicated evaluation organization (one per corpus
folder, owned by a system user), so its BM25 statistics and vectors are its own
and every question is recorded as an ordinary run: each one can be opened in the
AI inspector (``--share-with`` adds you to the organization).

Documents are re-indexed only when their content changes, so repeated runs over
the same corpus compare like with like.
"""

import hashlib
import logging
import re
import secrets
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.embeddings.embedder import Embedder
from ai_engine.evaluation.dataset import Question, document_files
from ai_engine.evaluation.metrics import QuestionOutcome
from ai_engine.generation.llm import LLMClient
from ai_engine.reranking.cross_encoder import Reranker
from ai_engine.retrieval.engine import RequestedStrategy
from ai_engine.retrieval.vector_store import QdrantVectorStore
from app.core.database import rls_bypass, set_rls_context
from app.core.security import hash_password
from app.core.storage import ObjectStorage
from app.models import (
    AIRun,
    Document,
    DocumentStatus,
    Organization,
    OrganizationMember,
    OrgRole,
    RetrievalResult,
    User,
)
from app.repositories.documents import DocumentRepository
from app.repositories.organizations import OrganizationRepository
from app.repositories.users import UserRepository
from app.schemas.answers import AskRequest
from app.schemas.organizations import OrganizationCreate
from app.schemas.retrieval import RetrieveRequest
from app.services.answering import AnswerService
from app.services.documents import DocumentService
from app.services.ingestion import IngestionLimits, IngestionService, Outcome
from app.services.organizations import OrganizationService
from app.services.retrieval import RetrievalService

logger = logging.getLogger(__name__)

EVALUATION_USER_EMAIL = "evaluation-runner@example.com"
_MARKDOWN_TITLE = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.MULTILINE)


class EvaluationError(Exception):
    pass


@dataclass(frozen=True)
class RunOptions:
    strategy: RequestedStrategy = RequestedStrategy.AUTO
    rerank: bool = True
    top_k: int = 5
    retrieval_only: bool = False


@dataclass(frozen=True)
class CorpusDocument:
    file: str
    sha256: str
    title: str
    document_id: uuid.UUID
    chunks: int
    indexed_now: bool  # False: unchanged since an earlier run, reused


@dataclass
class Workspace:
    user: User
    member: OrganizationMember
    organization: Organization
    documents: list[CorpusDocument]


def _title(path: Path, content: bytes) -> str | None:
    """A Markdown document's first heading, used as its title."""
    if path.suffix.lower() not in (".md", ".markdown"):
        return None
    match = _MARKDOWN_TITLE.search(content.decode("utf-8", errors="replace"))
    return match.group(1)[:300] if match else None


def _slug(corpus: Path) -> str:
    name = re.sub(r"[^a-z0-9]+", "-", corpus.name.lower()).strip("-") or "corpus"
    return f"eval-{name}"[:60]


class EvaluationRunner:
    def __init__(
        self,
        session: AsyncSession,
        *,
        storage: ObjectStorage,
        embedder: Embedder,
        vector_store: QdrantVectorStore,
        reranker: Reranker,
        llm: LLMClient,
        limits: IngestionLimits,
    ) -> None:
        self.session = session
        self.storage = storage
        self.embedder = embedder
        self.vectors = vector_store
        self.reranker = reranker
        self.llm = llm
        self.limits = limits

    # --- Workspace -------------------------------------------------------------------------

    async def prepare(self, corpus: Path, *, share_with: str | None = None) -> Workspace:
        """The evaluation organization for ``corpus``, with its documents indexed."""
        files = document_files(corpus)
        if not files:
            raise EvaluationError(f"No documents in {corpus}")
        user, member, organization = await self._organization(corpus, share_with)
        await set_rls_context(self.session, user_id=user.id, organization_id=organization.id)
        documents = await self._sync_documents(member, user, files)
        return Workspace(user=user, member=member, organization=organization, documents=documents)

    async def _organization(
        self, corpus: Path, share_with: str | None
    ) -> tuple[User, OrganizationMember, Organization]:
        users = UserRepository(self.session)
        orgs = OrganizationRepository(self.session)
        # Setting up a system workspace spans users and organizations.
        async with rls_bypass(self.session):
            user = await users.get_by_email(EVALUATION_USER_EMAIL)
            if user is None:
                user = User(
                    id=uuid.uuid4(),
                    email=EVALUATION_USER_EMAIL,
                    # Nobody signs in as this user: the password is random and discarded.
                    password_hash=hash_password(secrets.token_urlsafe(32)),
                    full_name="Evaluation runner",
                    is_active=True,
                )
                users.add(user)
                await self.session.commit()

            slug = _slug(corpus)
            organization = await orgs.get_by_slug(slug)
            if organization is None:
                member = await OrganizationService(self.session).create(
                    user, OrganizationCreate(name=f"Evaluation: {corpus.name}", slug=slug)
                )
                organization = member.organization
            else:
                found = await orgs.get_membership(organization.id, user.id)
                if found is None:
                    raise EvaluationError(
                        f"The organization {slug!r} isn't an evaluation workspace"
                    )
                member = found

            if share_with:
                viewer = await users.get_by_email(share_with.lower())
                if viewer is None:
                    raise EvaluationError(f"No user with the email {share_with}")
                if await orgs.get_membership(organization.id, viewer.id) is None:
                    await set_rls_context(self.session, organization_id=organization.id)
                    orgs.add(
                        OrganizationMember(
                            organization_id=organization.id, user_id=viewer.id, role=OrgRole.ADMIN
                        )
                    )
                    await self.session.commit()
        return user, member, organization

    async def _sync_documents(
        self, member: OrganizationMember, user: User, files: Sequence[Path]
    ) -> list[CorpusDocument]:
        repository = DocumentRepository(self.session)
        service = DocumentService(self.session, storage=self.storage, vector_store=self.vectors)
        existing, _ = await repository.find(
            member.organization_id, statuses=[], search=None, limit=10_000, offset=0
        )
        by_file: dict[str, Document] = {d.filename: d for d in existing}

        corpus: list[CorpusDocument] = []
        for path in files:
            content = path.read_bytes()
            sha256 = hashlib.sha256(content).hexdigest()
            title = _title(path, content)
            current = by_file.pop(path.name, None)
            if (
                current is not None
                and current.sha256 == sha256
                and current.status is DocumentStatus.INDEXED
                and current.embedding_model == self.embedder.model_name
            ):
                corpus.append(
                    CorpusDocument(
                        path.name, sha256, current.title, current.id, current.chunk_count, False
                    )
                )
                continue
            if current is not None:
                await service.delete(member, current.id)
            document = await service.upload(
                member, user, filename=path.name, content=content, title=title
            )
            outcome = await IngestionService(
                self.session, self.storage, self.embedder, self.vectors, self.limits
            ).run(document.id, document.ingestion_id)
            indexed = await repository.get(member.organization_id, document.id)
            if outcome is not Outcome.INDEXED or indexed is None:
                error = indexed.error if indexed else "document missing"
                raise EvaluationError(f"Couldn't index {path.name}: {error}")
            logger.info("Indexed %s (%d chunks)", path.name, indexed.chunk_count)
            corpus.append(
                CorpusDocument(
                    path.name, sha256, indexed.title, indexed.id, indexed.chunk_count, True
                )
            )

        for stale in by_file.values():  # removed from the corpus folder
            await service.delete(member, stale.id)
        return corpus

    # --- Questions ---------------------------------------------------------------------------

    async def run(
        self,
        workspace: Workspace,
        questions: Sequence[Question],
        options: RunOptions,
        *,
        progress: Callable[[int, int, QuestionOutcome], None] | None = None,
    ) -> list[QuestionOutcome]:
        outcomes: list[QuestionOutcome] = []
        for number, question in enumerate(questions, start=1):
            outcome = await self._ask(workspace, question, options)
            outcomes.append(outcome)
            if progress is not None:
                progress(number, len(questions), outcome)
        return outcomes

    async def _ask(
        self, workspace: Workspace, question: Question, options: RunOptions
    ) -> QuestionOutcome:
        retrieval = RetrievalService(
            self.session, embedder=self.embedder, vector_store=self.vectors, reranker=self.reranker
        )
        run: AIRun
        results: list[RetrievalResult]
        if options.retrieval_only:
            run, results = await retrieval.retrieve(
                workspace.member,
                workspace.user,
                RetrieveRequest(
                    query=question.question,
                    strategy=options.strategy,
                    rerank=options.rerank,
                    top_k=options.top_k,
                ),
            )
        else:
            run, results = await AnswerService(
                self.session, retrieval=retrieval, llm=self.llm
            ).answer(
                workspace.member,
                workspace.user,
                AskRequest(
                    query=question.question,
                    strategy=options.strategy,
                    rerank=options.rerank,
                    top_k=options.top_k,
                ),
            )

        outcome = QuestionOutcome(
            question=question,
            ranked_passages=[r.chunk.content if r.chunk else "" for r in results],
            top_k=options.top_k,
            run_id=str(run.id),
            latency_ms=run.latency_ms,
        )
        answer = run.answer
        if answer is not None:
            by_id = {r.id: r for r in results}
            cited: list[str] = []
            for citation in answer.citations:
                result = by_id.get(citation.retrieval_result_id)
                text = result.chunk.content if result and result.chunk else ""
                if text not in cited:
                    cited.append(text)
            outcome.status = answer.status.value
            outcome.answer = answer.text
            outcome.cited_passages = cited
            outcome.invalid_citations = len(answer.invalid_citations)
            outcome.generation_ms = answer.latency_ms
            outcome.error = answer.error
            outcome.model = answer.model
            outcome.model_version = answer.model_version
        return outcome
