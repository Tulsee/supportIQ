import uuid
from typing import Any, Literal, Self

from pydantic import BaseModel, Field

from ai_engine.generation.llm import ModelStatus
from ai_engine.retrieval.engine import RequestedStrategy
from app.models import AIAnswer, AnswerStatus, Citation
from app.schemas.documents import DocumentSummary
from app.schemas.retrieval import Query


class AskRequest(BaseModel):
    query: Query
    strategy: RequestedStrategy = Field(
        default=RequestedStrategy.AUTO, description="How to search for evidence."
    )
    rerank: bool = Field(default=True, description="Reorder candidates with the cross-encoder.")
    top_k: int = Field(default=5, ge=1, le=10, description="Passages to answer from.")


class CitationRead(BaseModel):
    passage_number: int = Field(
        description="The [n] marker in the answer; also the passage's final rank in the run."
    )
    answer_start: int = Field(description="The cited claim: a character range of `text`.")
    answer_end: int
    document_id: uuid.UUID | None
    document: DocumentSummary | None = Field(description="Null if the document was deleted.")
    document_version: int
    chunk_id: uuid.UUID | None = Field(description="Null if the passage was re-indexed away.")
    chunk_index: int
    page_number: int | None
    page_end: int | None
    section_heading: str | None

    @classmethod
    def build(cls, citation: Citation) -> Self:
        document = citation.document
        return cls(
            passage_number=citation.passage_number,
            answer_start=citation.answer_start,
            answer_end=citation.answer_end,
            document_id=citation.document_id,
            document=(
                DocumentSummary(id=document.id, title=document.title, file_type=document.file_type)
                if document is not None
                else None
            ),
            document_version=citation.document_version,
            chunk_id=citation.chunk_id,
            chunk_index=citation.chunk_index,
            page_number=citation.page_number,
            page_end=citation.page_end,
            section_heading=citation.section_heading,
        )


class AnswerRead(BaseModel):
    status: AnswerStatus = Field(
        description="`answered`: cites the passages; `uncited`: answered without citing "
        "(unverified); `declined`: the model found no answer in the passages; "
        "`no_passages`: nothing was retrieved, the model wasn't asked; `failed`: see `error`."
    )
    text: str | None = Field(
        description="The answer, with [n] citation markers. Numbers the model invented "
        "are removed (see `invalid_citations`)."
    )
    error: str | None
    citations: list[CitationRead] = Field(description="In reading order.")
    invalid_citations: list[int] = Field(
        description="Passage numbers the model cited but was never given."
    )
    provider: str
    model: str = Field(description="The model that answered, as the provider reported it.")
    model_version: str | None = Field(description="E.g. the Ollama model digest.")
    config: dict[str, Any] = Field(description="Sampling, output and context settings.")
    prompt_version: str
    system_prompt: str
    user_prompt: str = Field(description="The question and the numbered passages.")
    raw_output: str | None = Field(description="Exactly what the model returned.")
    passage_count: int = Field(description="Passages in the prompt (citable).")
    omitted_passages: int = Field(
        description="Selected passages left out to fit the model's context window."
    )
    stop_reason: str | None = Field(
        description="`stop`, `length` (hit the output limit), `refusal`, or the provider's."
    )
    input_tokens: int | None
    output_tokens: int | None
    timings_ms: dict[str, float]
    latency_ms: float

    @classmethod
    def build(cls, answer: AIAnswer) -> Self:
        return cls(
            status=answer.status,
            text=answer.text,
            error=answer.error,
            citations=[CitationRead.build(c) for c in answer.citations],
            invalid_citations=answer.invalid_citations,
            provider=answer.provider,
            model=answer.model,
            model_version=answer.model_version,
            config=answer.config,
            prompt_version=answer.prompt_version,
            system_prompt=answer.system_prompt,
            user_prompt=answer.user_prompt,
            raw_output=answer.raw_output,
            passage_count=answer.passage_count,
            omitted_passages=answer.omitted_passages,
            stop_reason=answer.stop_reason,
            input_tokens=answer.input_tokens,
            output_tokens=answer.output_tokens,
            timings_ms=answer.timings,
            latency_ms=answer.latency_ms,
        )


class ModelStatusRead(BaseModel):
    provider: str
    model: str
    status: Literal["ready", "unreachable", "not_installed", "not_configured"] = Field(
        description="`ready`: it can answer now. Otherwise questions fail until it is "
        "started, installed or configured."
    )
    detail: str | None

    @classmethod
    def build(cls, provider: str, model: str, status: ModelStatus) -> Self:
        return cls(provider=provider, model=model, status=status.state, detail=status.detail)
