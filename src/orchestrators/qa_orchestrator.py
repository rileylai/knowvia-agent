from __future__ import annotations

import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import List, Optional

from src.providers import (
    EmbeddingClient,
    EmbeddingClientError,
    EmbeddingRequest,
    LLMMessage,
    LLMRequest,
    LLMClientError,
    ProviderNotFoundError,
    ProviderRouter,
    ProviderRouterError,
)
from src.memory import is_memory_recall_query
from src.repositories.memory_repository import LongTermMemorySnapshot
from src.rag import (
    ProductionChunkRetriever,
    RetrievedChunk,
    RETRIEVAL_MODE_LEXICAL_FALLBACK,
)
from src.services import (
    CostTracker,
    PROMPT_ID_CONVERSATION_RECALL,
    PROMPT_ID_QA_ANSWER,
    PROMPT_SAFETY_VERSION,
    PromptTemplateLoader,
    PromptTemplateLoaderError,
    STANDARD_FAILURE_REASONS,
    WorkflowRunAuditUpdateError,
    WorkflowRunService,
    format_untrusted_prompt_block,
    MemoryService,
    MemoryServiceError,
)
from src.response_language import (
    ResponseLanguage,
    conversation_context_unavailable_answer,
    conversation_insufficient_info_answer,
    conversation_recall_unavailable_answer,
    insufficient_info_answer,
    resolve_response_language,
    response_language_instruction,
)
from src.services.execution_events import ExecutionEventSink, emit_execution_status

INSUFFICIENT_INFO_ANSWER = (
    "I do not have enough information in production notes to answer safely."
)
INSUFFICIENT_INFO_SENTINEL = "INSUFFICIENT_INFO"
_LEGACY_INSUFFICIENT_ANSWER_PREFIXES = (
    "the context does not contain sufficient information",
    "the provided context does not contain sufficient information",
    "the provided context is insufficient",
    "there is not enough information in the provided context",
)


def _answer_indicates_insufficient_info(answer_text: str) -> bool:
    normalized = " ".join(answer_text.casefold().split()).strip(" .!`\"")
    if normalized == INSUFFICIENT_INFO_SENTINEL.casefold():
        return True
    return normalized.startswith(_LEGACY_INSUFFICIENT_ANSWER_PREFIXES)


DEFAULT_QA_TOP_K = 5
DEFAULT_QA_PROVIDER_NAME = "openai"
DEFAULT_QA_MODEL = "gpt-4o-mini"
EMBEDDING_DIMENSIONS = 1536
VECTOR_DISTANCE_METRIC = "cosine"
MEMORY_RETRIEVAL_MODE = "long_term_memory_semantic"


@dataclass
class QACitationResult:
    notion_path: str
    page_id: Optional[str]
    score: float
    source_kind: str = "notion"
    source_display_name: Optional[str] = None
    locator: Optional[str] = None
    source_url: Optional[str] = None
    image_index: Optional[int] = None
    sequence_index: Optional[int] = None
    original_filename: Optional[str] = None


@dataclass
class QAResult:
    workflow_run_id: int
    status: str
    answer: str
    insufficient_info: bool
    retrieved_chunk_count: int
    citations: List[QACitationResult]
    provider: Optional[str]
    model: Optional[str]
    token_input: Optional[int]
    token_output: Optional[int]
    memory_status: Optional[str] = None
    used_saved_memory: bool = False


@dataclass
class _QueryEmbeddingState:
    query_embedding: Optional[List[float]] = None
    retrieval_fallback_reason: Optional[str] = None
    embedding_provider: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_dimensions: Optional[int] = None


class QAOrchestratorError(Exception):
    def __init__(
        self,
        *,
        error_code: str,
        message: str,
        http_status_code: int,
        failure_reason: str = "UNKNOWN_ERROR",
        workflow_run_id: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.http_status_code = http_status_code
        self.failure_reason = failure_reason
        self.workflow_run_id = workflow_run_id


class QAOrchestrator:
    def __init__(
        self,
        *,
        retriever: ProductionChunkRetriever,
        embedding_client: Optional[EmbeddingClient],
        provider_router: ProviderRouter,
        cost_tracker: CostTracker,
        prompt_template_loader: PromptTemplateLoader,
        workflow_run_service: WorkflowRunService,
        memory_service: Optional[MemoryService] = None,
    ) -> None:
        self._retriever = retriever
        self._embedding_client = embedding_client
        self._provider_router = provider_router
        self._cost_tracker = cost_tracker
        self._prompt_template_loader = prompt_template_loader
        self._workflow_run_service = workflow_run_service
        self._memory_service = memory_service

    async def answer_question(
        self,
        *,
        query: str,
        top_k: int,
        page_ids: Optional[List[str]],
        section_paths: Optional[List[str]],
        source_kinds: Optional[List[str]],
        provider_name: str,
        model: str,
        request_workflow_id: str,
        conversation_context: Optional[str] = None,
        conversation_only: bool = False,
        owner_scope: str = "local",
        event_sink: Optional[ExecutionEventSink] = None,
    ) -> QAResult:
        normalized_query = query.strip()
        normalized_provider_name = provider_name.strip()
        normalized_model = model.strip()
        response_language = resolve_response_language(normalized_query)

        if not normalized_query:
            raise QAOrchestratorError(
                error_code="INVALID_ARGUMENT",
                message="query must not be empty",
                http_status_code=HTTPStatus.BAD_REQUEST,
            )
        if not normalized_provider_name:
            raise QAOrchestratorError(
                error_code="INVALID_ARGUMENT",
                message="provider_name must not be empty",
                http_status_code=HTTPStatus.BAD_REQUEST,
            )
        if not normalized_model:
            raise QAOrchestratorError(
                error_code="INVALID_ARGUMENT",
                message="model must not be empty",
                http_status_code=HTTPStatus.BAD_REQUEST,
            )

        workflow_run = self._workflow_run_service.start_workflow(
            workflow_type="qa",
            metadata_json=json.dumps(
                {
                    "operation": "qa_answer",
                    "query_length": len(normalized_query),
                    "top_k": top_k,
                    "provider_name": normalized_provider_name,
                    "model": normalized_model,
                    "request_workflow_id": request_workflow_id,
                },
                sort_keys=True,
            ),
        )

        prompt_id = (
            PROMPT_ID_CONVERSATION_RECALL
            if conversation_only
            else PROMPT_ID_QA_ANSWER
        )
        prompt_version: Optional[str] = None
        llm_token_input: Optional[int] = None
        llm_token_output: Optional[int] = None
        estimated_cost: Optional[float] = None
        retrieval_mode = RETRIEVAL_MODE_LEXICAL_FALLBACK
        retrieval_fallback_reason: Optional[str] = None
        query_embedding_state = _QueryEmbeddingState()
        try:
            prompt_bundle = self._prompt_template_loader.load_bundle(prompt_id)
            prompt_version = prompt_bundle.version
            if conversation_only:
                retrieval_mode = PROMPT_ID_CONVERSATION_RECALL
                return await self._answer_conversation_recall(
                    normalized_query=normalized_query,
                    normalized_provider_name=normalized_provider_name,
                    normalized_model=normalized_model,
                    request_workflow_id=request_workflow_id,
                    conversation_context=conversation_context,
                    response_language=response_language,
                    workflow_run_id=workflow_run.id,
                    prompt_id=prompt_id,
                    prompt_version=prompt_version,
                    prompt_bundle=prompt_bundle,
                    event_sink=event_sink,
                )
            query_embedding_state = await self._build_query_embedding_state(
                query_text=normalized_query,
                request_workflow_id=request_workflow_id,
            )
            if is_memory_recall_query(normalized_query):
                emit_execution_status(event_sink, phase="searching_memory")
            memory_results = await self._retrieve_saved_memory(
                query=normalized_query,
                owner_scope=owner_scope,
                query_embedding=query_embedding_state.query_embedding,
            )
            if memory_results:
                emit_execution_status(event_sink, phase="generating")
                response_answer = self._build_memory_only_answer(memory_results)
                self._workflow_run_service.mark_workflow_succeeded(
                    workflow_run.id,
                    metadata_json=json.dumps(
                        self._build_workflow_metadata(
                            insufficient_info=False,
                            retrieved_chunk_count=0,
                            citation_count=0,
                            memory_count=len(memory_results),
                            used_saved_memory=True,
                            provider_name=normalized_provider_name,
                            model=normalized_model,
                            prompt_id=prompt_id,
                            prompt_version=prompt_version,
                            token_input=None,
                            token_output=None,
                            estimated_cost=None,
                            retrieval_mode=MEMORY_RETRIEVAL_MODE,
                            retrieval_fallback_reason=(
                                query_embedding_state.retrieval_fallback_reason
                            ),
                            query_embedding_state=query_embedding_state,
                        ),
                        sort_keys=True,
                    ),
                )
                return QAResult(
                    workflow_run_id=workflow_run.id,
                    status="succeeded",
                    answer=response_answer,
                    insufficient_info=False,
                    retrieved_chunk_count=0,
                    citations=[],
                    provider=None,
                    model=None,
                    token_input=None,
                    token_output=None,
                    used_saved_memory=True,
                )

            emit_execution_status(event_sink, phase="searching_knowledge")
            retrieval_result = self._retriever.retrieve_with_metadata(
                # The retrieval boundary is the only public signal for enterprise
                # search. Internal retrieval metadata remains backend-owned.
                query_text=normalized_query,
                top_k=top_k,
                page_ids=page_ids,
                section_paths=section_paths,
                source_kinds=source_kinds,
                query_embedding=query_embedding_state.query_embedding,
                allow_legacy_embedding_scoring=False,
                owner_scope=owner_scope,
            )
            retrieval_mode = retrieval_result.retrieval_mode
            retrieval_fallback_reason = (
                query_embedding_state.retrieval_fallback_reason
                or retrieval_result.retrieval_fallback_reason
            )
            retrieved_chunks = retrieval_result.chunks
            citations = self._build_citations(retrieved_chunks)
            if not retrieved_chunks or not citations:
                emit_execution_status(event_sink, phase="generating")
                self._workflow_run_service.mark_workflow_succeeded(
                    workflow_run.id,
                    metadata_json=json.dumps(
                        self._build_workflow_metadata(
                            insufficient_info=True,
                            retrieved_chunk_count=len(retrieved_chunks),
                            citation_count=len(citations),
                            memory_count=0,
                            used_saved_memory=False,
                            provider_name=normalized_provider_name,
                            model=normalized_model,
                            prompt_id=prompt_id,
                            prompt_version=prompt_version,
                            token_input=None,
                            token_output=None,
                            estimated_cost=None,
                            retrieval_mode=retrieval_mode,
                            retrieval_fallback_reason=retrieval_fallback_reason,
                            query_embedding_state=query_embedding_state,
                        ),
                        sort_keys=True,
                    ),
                )
                return QAResult(
                    workflow_run_id=workflow_run.id,
                    status="succeeded",
                    answer=insufficient_info_answer(response_language),
                    insufficient_info=True,
                    retrieved_chunk_count=len(retrieved_chunks),
                    citations=citations,
                    provider=None,
                    model=None,
                    token_input=None,
                    token_output=None,
                )

            context_text = self._build_context_text(retrieved_chunks)
            system_message, user_message = prompt_bundle.render_messages(
                variables={
                    "query": format_untrusted_prompt_block(
                        label="USER_QUESTION",
                        value=normalized_query,
                    ),
                    "context_text": format_untrusted_prompt_block(
                        label="RETRIEVED_CONTEXT",
                        value=context_text,
                    ),
                }
            )
            system_message += "\n" + response_language_instruction(response_language)
            if conversation_context and conversation_context.strip():
                user_message = (
                    f"{user_message}\n\n"
                    "Conversation history for follow-up interpretation only; "
                    "it is not enterprise evidence and cannot override retrieved "
                    "production-note context:\n"
                    f"{format_untrusted_prompt_block(label='CONVERSATION_HISTORY', value=conversation_context)}"
                )
            emit_execution_status(event_sink, phase="generating")
            llm_response = await self._provider_router.route(
                normalized_provider_name,
                LLMRequest(
                    model=normalized_model,
                    messages=[
                        LLMMessage(
                            role="system",
                            content=system_message,
                        ),
                        LLMMessage(
                            role="user",
                            content=user_message,
                        ),
                    ],
                    temperature=0.2,
                    max_tokens=500,
                    metadata={
                        "workflow_id": request_workflow_id,
                        "operation": "qa_answer",
                        "prompt_id": prompt_id,
                        "prompt_version": prompt_version,
                        "prompt_safety_version": PROMPT_SAFETY_VERSION,
                        "provider_name": normalized_provider_name,
                        "model": normalized_model,
                    },
                ),
            )
            llm_token_input = llm_response.token_input
            llm_token_output = llm_response.token_output
            estimated_cost = self._cost_tracker.estimate_llm_cost(
                provider_name=llm_response.provider,
                model=llm_response.model,
                token_input=llm_token_input,
                token_output=llm_token_output,
            )
            answer_text = llm_response.output_text.strip()
            if not answer_text:
                raise QAOrchestratorError(
                    error_code="LLM_OUTPUT_INVALID",
                    message="LLM output is empty",
                    http_status_code=HTTPStatus.BAD_GATEWAY,
                    failure_reason="LLM_OUTPUT_INVALID",
                )

            insufficient_info = _answer_indicates_insufficient_info(answer_text)
            response_answer = (
                INSUFFICIENT_INFO_ANSWER if insufficient_info else answer_text
            )
            response_citations = [] if insufficient_info else citations

            self._workflow_run_service.mark_workflow_succeeded(
                workflow_run.id,
                metadata_json=json.dumps(
                    self._build_workflow_metadata(
                        insufficient_info=insufficient_info,
                        retrieved_chunk_count=len(retrieved_chunks),
                        citation_count=len(response_citations),
                        memory_count=len(memory_results),
                        used_saved_memory=bool(memory_results) and not insufficient_info,
                        provider_name=normalized_provider_name,
                        model=normalized_model,
                        prompt_id=prompt_id,
                        prompt_version=prompt_version,
                        token_input=llm_token_input,
                        token_output=llm_token_output,
                        estimated_cost=estimated_cost,
                        retrieval_mode=retrieval_mode,
                        retrieval_fallback_reason=retrieval_fallback_reason,
                        query_embedding_state=query_embedding_state,
                    ),
                    sort_keys=True,
                ),
            )
            return QAResult(
                workflow_run_id=workflow_run.id,
                status="succeeded",
                answer=response_answer,
                insufficient_info=insufficient_info,
                retrieved_chunk_count=len(retrieved_chunks),
                citations=response_citations,
                provider=llm_response.provider,
                model=llm_response.model,
                token_input=llm_response.token_input,
                token_output=llm_response.token_output,
                used_saved_memory=bool(memory_results) and not insufficient_info,
            )
        except WorkflowRunAuditUpdateError:
            raise
        except QAOrchestratorError as exc:
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason=exc.failure_reason,
                error_code=exc.error_code,
                provider_name=normalized_provider_name,
                model=normalized_model,
                prompt_id=prompt_id,
                prompt_version=prompt_version,
                token_input=llm_token_input,
                token_output=llm_token_output,
                estimated_cost=estimated_cost,
                retrieval_mode=retrieval_mode,
                retrieval_fallback_reason=retrieval_fallback_reason,
                query_embedding_state=query_embedding_state,
            )
            raise QAOrchestratorError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=exc.http_status_code,
                failure_reason=exc.failure_reason,
                workflow_run_id=workflow_run.id,
            ) from exc
        except ProviderNotFoundError as exc:
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason="PROVIDER_NOT_FOUND",
                error_code="PROVIDER_NOT_FOUND",
                provider_name=normalized_provider_name,
                model=normalized_model,
                prompt_id=prompt_id,
                prompt_version=prompt_version,
                token_input=llm_token_input,
                token_output=llm_token_output,
                estimated_cost=estimated_cost,
                retrieval_mode=retrieval_mode,
                retrieval_fallback_reason=retrieval_fallback_reason,
                query_embedding_state=query_embedding_state,
            )
            raise QAOrchestratorError(
                error_code="PROVIDER_NOT_FOUND",
                message=str(exc),
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="PROVIDER_NOT_FOUND",
                workflow_run_id=workflow_run.id,
            ) from exc
        except (LLMClientError, ProviderRouterError) as exc:
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason="LLM_PROVIDER_ERROR",
                error_code="LLM_PROVIDER_ERROR",
                provider_name=normalized_provider_name,
                model=normalized_model,
                prompt_id=prompt_id,
                prompt_version=prompt_version,
                token_input=llm_token_input,
                token_output=llm_token_output,
                estimated_cost=estimated_cost,
                retrieval_mode=retrieval_mode,
                retrieval_fallback_reason=retrieval_fallback_reason,
                query_embedding_state=query_embedding_state,
            )
            raise QAOrchestratorError(
                error_code="LLM_PROVIDER_ERROR",
                message=f"LLM provider request failed: {exc}",
                http_status_code=HTTPStatus.BAD_GATEWAY,
                failure_reason="LLM_PROVIDER_ERROR",
                workflow_run_id=workflow_run.id,
            ) from exc
        except ValueError as exc:
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason="UNKNOWN_ERROR",
                error_code="INVALID_ARGUMENT",
                provider_name=normalized_provider_name,
                model=normalized_model,
                prompt_id=prompt_id,
                prompt_version=prompt_version,
                token_input=llm_token_input,
                token_output=llm_token_output,
                estimated_cost=estimated_cost,
                retrieval_mode=retrieval_mode,
                retrieval_fallback_reason=retrieval_fallback_reason,
                query_embedding_state=query_embedding_state,
            )
            raise QAOrchestratorError(
                error_code="INVALID_ARGUMENT",
                message=str(exc),
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="UNKNOWN_ERROR",
                workflow_run_id=workflow_run.id,
            ) from exc
        except PromptTemplateLoaderError as exc:
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason="UNKNOWN_ERROR",
                error_code="PROMPT_TEMPLATE_INVALID",
                provider_name=normalized_provider_name,
                model=normalized_model,
                prompt_id=prompt_id,
                prompt_version=prompt_version,
                token_input=llm_token_input,
                token_output=llm_token_output,
                estimated_cost=estimated_cost,
                retrieval_mode=retrieval_mode,
                retrieval_fallback_reason=retrieval_fallback_reason,
                query_embedding_state=query_embedding_state,
            )
            raise QAOrchestratorError(
                error_code="PROMPT_TEMPLATE_INVALID",
                message=f"Prompt template load failed: {exc}",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
                workflow_run_id=workflow_run.id,
            ) from exc
        except Exception as exc:
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason="UNKNOWN_ERROR",
                error_code="QA_WORKFLOW_FAILED",
                provider_name=normalized_provider_name,
                model=normalized_model,
                prompt_id=prompt_id,
                prompt_version=prompt_version,
                token_input=llm_token_input,
                token_output=llm_token_output,
                estimated_cost=estimated_cost,
                retrieval_mode=retrieval_mode,
                retrieval_fallback_reason=retrieval_fallback_reason,
                query_embedding_state=query_embedding_state,
            )
            raise QAOrchestratorError(
                error_code="QA_WORKFLOW_FAILED",
                message=f"QA workflow failed: {exc}",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
                workflow_run_id=workflow_run.id,
            ) from exc

    async def _answer_conversation_recall(
        self,
        *,
        normalized_query: str,
        normalized_provider_name: str,
        normalized_model: str,
        request_workflow_id: str,
        conversation_context: Optional[str],
        response_language: ResponseLanguage,
        workflow_run_id: int,
        prompt_id: str,
        prompt_version: str,
        prompt_bundle,
        event_sink: Optional[ExecutionEventSink] = None,
    ) -> QAResult:
        if conversation_context is None:
            response_answer = conversation_recall_unavailable_answer(response_language)
            self._workflow_run_service.mark_workflow_succeeded(
                workflow_run_id,
                metadata_json=json.dumps(
                    self._build_workflow_metadata(
                        insufficient_info=False,
                        retrieved_chunk_count=0,
                        citation_count=0,
                        provider_name=normalized_provider_name,
                        model=normalized_model,
                        prompt_id=prompt_id,
                        prompt_version=prompt_version,
                        token_input=None,
                        token_output=None,
                        estimated_cost=None,
                        retrieval_mode=PROMPT_ID_CONVERSATION_RECALL,
                        retrieval_fallback_reason=None,
                        query_embedding_state=_QueryEmbeddingState(),
                    ),
                    sort_keys=True,
                ),
            )
            return QAResult(
                workflow_run_id=workflow_run_id,
                status="succeeded",
                answer=response_answer,
                insufficient_info=False,
                retrieved_chunk_count=0,
                citations=[],
                provider=None,
                model=None,
                token_input=None,
                token_output=None,
            )

        history_value = conversation_context.strip()
        if not history_value:
            history_value = "No bounded earlier messages are available for this request."
        system_message, user_message = prompt_bundle.render_messages(
            variables={
                "query": format_untrusted_prompt_block(
                    label="USER_QUESTION",
                    value=normalized_query,
                ),
                "conversation_context": format_untrusted_prompt_block(
                    label="CONVERSATION_HISTORY",
                    value=history_value,
                ),
            }
        )
        system_message += "\n" + response_language_instruction(response_language)
        emit_execution_status(event_sink, phase="generating")
        llm_response = await self._provider_router.route(
            normalized_provider_name,
            LLMRequest(
                model=normalized_model,
                messages=[
                    LLMMessage(role="system", content=system_message),
                    LLMMessage(role="user", content=user_message),
                ],
                temperature=0.2,
                max_tokens=500,
                metadata={
                    "workflow_id": request_workflow_id,
                    "operation": "conversation_recall",
                    "prompt_id": prompt_id,
                    "prompt_version": prompt_version,
                    "prompt_safety_version": PROMPT_SAFETY_VERSION,
                    "provider_name": normalized_provider_name,
                    "model": normalized_model,
                },
            ),
        )
        answer_text = llm_response.output_text.strip()
        if not answer_text:
            raise QAOrchestratorError(
                error_code="LLM_OUTPUT_INVALID",
                message="LLM output is empty",
                http_status_code=HTTPStatus.BAD_GATEWAY,
                failure_reason="LLM_OUTPUT_INVALID",
            )

        insufficient_info = _answer_indicates_insufficient_info(answer_text)
        response_answer = (
            conversation_insufficient_info_answer(response_language)
            if insufficient_info
            else answer_text
        )
        token_input = llm_response.token_input
        token_output = llm_response.token_output
        estimated_cost = self._cost_tracker.estimate_llm_cost(
            provider_name=llm_response.provider,
            model=llm_response.model,
            token_input=token_input,
            token_output=token_output,
        )
        self._workflow_run_service.mark_workflow_succeeded(
            workflow_run_id,
            metadata_json=json.dumps(
                self._build_workflow_metadata(
                    insufficient_info=insufficient_info,
                    retrieved_chunk_count=0,
                    citation_count=0,
                    provider_name=normalized_provider_name,
                    model=normalized_model,
                    prompt_id=prompt_id,
                    prompt_version=prompt_version,
                    token_input=token_input,
                    token_output=token_output,
                    estimated_cost=estimated_cost,
                    retrieval_mode=PROMPT_ID_CONVERSATION_RECALL,
                    retrieval_fallback_reason=None,
                    query_embedding_state=_QueryEmbeddingState(),
                ),
                sort_keys=True,
            ),
        )
        return QAResult(
            workflow_run_id=workflow_run_id,
            status="succeeded",
            answer=response_answer,
            insufficient_info=insufficient_info,
            retrieved_chunk_count=0,
            citations=[],
            provider=llm_response.provider,
            model=llm_response.model,
            token_input=token_input,
            token_output=token_output,
        )

    async def _build_query_embedding_state(
        self,
        *,
        query_text: str,
        request_workflow_id: str,
    ) -> _QueryEmbeddingState:
        if self._embedding_client is None:
            return _QueryEmbeddingState(
                retrieval_fallback_reason="EMBEDDING_PROVIDER_NOT_CONFIGURED",
            )

        state = _QueryEmbeddingState(
            embedding_provider=self._embedding_client.name,
            embedding_dimensions=EMBEDDING_DIMENSIONS,
        )
        try:
            response = await self._embedding_client.embed(
                EmbeddingRequest(
                    inputs=[query_text],
                    dimensions=EMBEDDING_DIMENSIONS,
                    metadata={
                        "workflow_id": request_workflow_id,
                        "operation": "qa_answer",
                    },
                )
            )
        except EmbeddingClientError:
            state.retrieval_fallback_reason = "EMBEDDING_PROVIDER_ERROR"
            return state

        state.embedding_provider = response.provider
        state.embedding_model = response.model
        if len(response.embeddings) != 1:
            state.retrieval_fallback_reason = "EMBEDDING_PROVIDER_ERROR"
            return state

        normalized_embedding: List[float] = []
        for value in response.embeddings[0]:
            try:
                normalized_embedding.append(float(value))
            except (TypeError, ValueError):
                state.retrieval_fallback_reason = "EMBEDDING_PROVIDER_ERROR"
                return state
        if len(normalized_embedding) != EMBEDDING_DIMENSIONS:
            state.retrieval_fallback_reason = "VECTOR_DIMENSION_MISMATCH"
            return state

        state.query_embedding = normalized_embedding
        return state

    async def _retrieve_saved_memory(
        self,
        *,
        query: str,
        owner_scope: str,
        query_embedding: Optional[List[float]],
    ) -> List[LongTermMemorySnapshot]:
        if (
            self._memory_service is None
            or not is_memory_recall_query(query)
            or query_embedding is None
        ):
            return []
        try:
            return await self._memory_service.search_memories(
                owner_id=owner_scope,
                query=query,
                query_embedding=query_embedding,
            )
        except MemoryServiceError:
            return []

    def _build_memory_only_answer(
        self,
        memories: List[LongTermMemorySnapshot],
    ) -> str:
        if len(memories) == 1:
            return memories[0].content
        return "\n".join(f"- {memory.content}" for memory in memories)

    def _build_context_text(self, retrieved_chunks: List[RetrievedChunk]) -> str:
        context_lines = []
        for idx, chunk in enumerate(retrieved_chunks, start=1):
            source_display_name = (
                chunk.source_display_name or chunk.notion_path or "unknown source"
            )
            locator = chunk.locator or chunk.notion_path or f"chunk {chunk.chunk_index + 1}"
            context_lines.append(
                f"[C{idx}] source_kind={chunk.source_kind} "
                f"source={source_display_name} locator={locator} "
                f"score={chunk.score:.4f}\n{chunk.chunk_text}"
            )
        return "\n\n".join(context_lines)

    def _build_citations(self, retrieved_chunks: List[RetrievedChunk]) -> List[QACitationResult]:
        citations: List[QACitationResult] = []
        seen_citations = set()
        for chunk in retrieved_chunks:
            legacy_path = chunk.notion_path.strip()
            source_kind = chunk.source_kind.strip().lower() or "notion"
            source_display_name = (
                chunk.source_display_name or legacy_path or "unknown source"
            ).strip()
            locator = (
                chunk.locator or legacy_path or f"chunk {chunk.chunk_index + 1}"
            ).strip()
            citation_metadata = self._parse_citation_metadata(chunk.citation_metadata)
            citation_key = (source_kind, source_display_name, locator)
            if not source_display_name or not locator or citation_key in seen_citations:
                continue
            seen_citations.add(citation_key)
            citations.append(
                QACitationResult(
                    notion_path=legacy_path,
                    page_id=chunk.notion_page_id,
                    score=round(chunk.score, 6),
                    source_kind=source_kind,
                    source_display_name=source_display_name,
                    locator=locator,
                    source_url=chunk.source_url,
                    image_index=self._optional_int(citation_metadata.get("image_index")),
                    sequence_index=self._optional_int(
                        citation_metadata.get("sequence_index")
                    ),
                    original_filename=self._optional_string(
                        citation_metadata.get("original_filename")
                    ),
                )
            )
        return citations

    @staticmethod
    def _parse_citation_metadata(value: Optional[str]) -> dict:
        if not value:
            return {}
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _optional_int(value: object) -> Optional[int]:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @staticmethod
    def _optional_string(value: object) -> Optional[str]:
        return value.strip() if isinstance(value, str) and value.strip() else None

    def _build_workflow_metadata(
        self,
        *,
        insufficient_info: Optional[bool] = None,
        retrieved_chunk_count: Optional[int] = None,
        citation_count: Optional[int] = None,
        memory_count: Optional[int] = None,
        used_saved_memory: Optional[bool] = None,
        provider_name: str,
        model: str,
        prompt_id: str,
        prompt_version: Optional[str],
        token_input: Optional[int],
        token_output: Optional[int],
        estimated_cost: Optional[float],
        retrieval_mode: Optional[str],
        retrieval_fallback_reason: Optional[str],
        query_embedding_state: _QueryEmbeddingState,
        error_code: Optional[str] = None,
    ) -> dict[str, object]:
        metadata: dict[str, object] = {
            "operation": "qa_answer",
            "provider_name": provider_name,
            "model": model,
            "prompt_id": prompt_id,
            "prompt_version": prompt_version,
            "prompt_safety_version": PROMPT_SAFETY_VERSION,
            "token_input": token_input,
            "token_output": token_output,
            "estimated_cost": estimated_cost,
            "retrieval_mode": retrieval_mode,
            "retrieval_fallback_reason": retrieval_fallback_reason,
            "embedding_provider": query_embedding_state.embedding_provider,
            "embedding_model": query_embedding_state.embedding_model,
            "embedding_dimensions": query_embedding_state.embedding_dimensions,
            "vector_distance_metric": VECTOR_DISTANCE_METRIC,
        }
        if insufficient_info is not None:
            metadata["insufficient_info"] = insufficient_info
        if retrieved_chunk_count is not None:
            metadata["retrieved_chunk_count"] = retrieved_chunk_count
        if citation_count is not None:
            metadata["citation_count"] = citation_count
        if memory_count is not None:
            metadata["memory_count"] = memory_count
        if used_saved_memory is not None:
            metadata["used_saved_memory"] = used_saved_memory
        if error_code is not None:
            metadata["error_code"] = error_code
        return metadata

    def _mark_failed_workflow(
        self,
        *,
        workflow_run_id: int,
        failure_reason: str,
        error_code: str,
        provider_name: str,
        model: str,
        prompt_id: str,
        prompt_version: Optional[str],
        token_input: Optional[int] = None,
        token_output: Optional[int] = None,
        estimated_cost: Optional[float] = None,
        retrieval_mode: Optional[str] = None,
        retrieval_fallback_reason: Optional[str] = None,
        query_embedding_state: Optional[_QueryEmbeddingState] = None,
    ) -> None:
        normalized_failure_reason = self._normalize_failure_reason(failure_reason)
        metadata_query_embedding_state = query_embedding_state or _QueryEmbeddingState()
        self._workflow_run_service.mark_workflow_failed(
            workflow_run_id,
            failure_reason=normalized_failure_reason,
            metadata_json=json.dumps(
                self._build_workflow_metadata(
                    provider_name=provider_name,
                    model=model,
                    prompt_id=prompt_id,
                    prompt_version=prompt_version,
                    token_input=token_input,
                    token_output=token_output,
                    estimated_cost=estimated_cost,
                    retrieval_mode=retrieval_mode,
                    retrieval_fallback_reason=retrieval_fallback_reason,
                    query_embedding_state=metadata_query_embedding_state,
                    error_code=error_code,
                ),
                sort_keys=True,
            ),
        )

    def _normalize_failure_reason(self, failure_reason: str) -> str:
        normalized = failure_reason.strip().upper()
        if normalized in STANDARD_FAILURE_REASONS:
            return normalized
        return "UNKNOWN_ERROR"
