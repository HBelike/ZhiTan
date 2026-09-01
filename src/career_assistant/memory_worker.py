"""会话压缩与长期记忆任务的轻量后台 Worker。"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from src.career_assistant.contracts import (
    ModelCapability,
    ModelSelectionMode,
    ModelSelectionRequest,
)
from src.career_assistant.conversation_memory import ConversationMemoryService
from src.career_assistant.career_memory_extraction import CareerMemoryExtractionService
from src.career_assistant.model_gateway import ModelGateway
from src.career_assistant.persistence.compaction_repository import (
    CareerCompactionRepository,
    CompactionJobRecord,
)
from src.career_assistant.persistence.memory_repository import (
    CareerMemoryJobRecord,
    CareerMemoryRepository,
)
from src.career_assistant.resume_normalizer import ResumeNormalizer, ResumeSection
from src.career_assistant.settings import CareerMemoryWorkerSettings


LOGGER = logging.getLogger(__name__)


class MemoryJobProcessor(Protocol):
    def process_compaction(self, job: CompactionJobRecord) -> None: ...

    def process_memory(self, job: CareerMemoryJobRecord) -> None: ...


class CareerMemoryJobProcessor:
    """使用触发回答的模型档案执行已领取压缩任务。"""

    def __init__(
        self,
        service: ConversationMemoryService,
        model_gateway: ModelGateway,
        extraction_service: CareerMemoryExtractionService | None = None,
        memory_repository: CareerMemoryRepository | None = None,
    ) -> None:
        self._service = service
        self._model_gateway = model_gateway
        self._extraction_service = extraction_service
        self._memory_repository = memory_repository

    def process_compaction(self, job: CompactionJobRecord) -> None:
        if job.requested_profile_id is None:
            raise LookupError("压缩任务缺少回答模型档案")
        resolution = self._model_gateway.resolve(
            job.organization_id,
            ModelSelectionRequest(
                mode=ModelSelectionMode.SPECIFIC_PROFILE,
                profile_id=job.requested_profile_id,
                required_capabilities=frozenset({ModelCapability.TEXT}),
            ),
        )
        policy = resolution.profile.context_policy
        target_tokens = max(
            1,
            int(policy.context_window_tokens * policy.compression_target_percent / 100)
            - policy.reserved_output_tokens,
        )
        self._service.compact_claimed(
            job,
            resolution,
            target_prompt_tokens=target_tokens,
        )

    def process_memory(self, job: CareerMemoryJobRecord) -> None:
        if self._extraction_service is None or self._memory_repository is None:
            raise RuntimeError("长期记忆抽取服务未配置")
        selection = (
            ModelSelectionRequest(
                mode=ModelSelectionMode.SPECIFIC_PROFILE,
                profile_id=job.requested_profile_id,
                required_capabilities=frozenset({ModelCapability.TEXT}),
            )
            if job.requested_profile_id is not None
            else ModelSelectionRequest(
                mode=ModelSelectionMode.FREE_QUOTA_FIRST,
                required_capabilities=frozenset({ModelCapability.TEXT}),
            )
        )
        resolution = self._model_gateway.resolve(job.organization_id, selection)
        source_text, source_message_id, career_space_id = (
            self._memory_repository.load_memory_job_source(job)
        )
        if job.job_kind == "resume_indexing":
            profile = ResumeNormalizer().normalize(source_text)
            allowed = {
                ResumeSection.WORK_EXPERIENCE,
                ResumeSection.EDUCATION,
                ResumeSection.CERTIFICATIONS,
                ResumeSection.PUBLICATIONS,
                ResumeSection.PROFILE,
                ResumeSection.SKILLS,
                ResumeSection.PROJECTS,
            }
            source_text = "\n\n".join(
                item.content for item in profile.sections if item.section in allowed
            )
        self._extraction_service.process_claimed(
            job,
            resolution,
            source_text,
            career_space_id=career_space_id,
            source_message_id=source_message_id,
        )


class CareerMemoryWorker:
    """以单独并发上限轮询会话压缩任务。"""

    def __init__(
        self,
        compaction_jobs: CareerCompactionRepository,
        processor: MemoryJobProcessor,
        settings: CareerMemoryWorkerSettings,
        memory_jobs: CareerMemoryRepository | None = None,
    ) -> None:
        settings.validate()
        self._compaction_jobs = compaction_jobs
        self._processor = processor
        self._settings = settings
        self._memory_jobs = memory_jobs
        self._stopping = asyncio.Event()
        self._active: set[asyncio.Task[None]] = set()

    async def run_forever(self) -> None:
        try:
            while not self._stopping.is_set():
                claimed_any = await self._fill_capacity()
                if self._active:
                    done, _ = await asyncio.wait(
                        self._active,
                        timeout=self._settings.poll_seconds,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    self._active.difference_update(done)
                    await asyncio.gather(*done)
                elif not claimed_any:
                    try:
                        await asyncio.wait_for(
                            self._stopping.wait(),
                            timeout=self._settings.poll_seconds,
                        )
                    except TimeoutError:
                        pass
        finally:
            if self._active:
                await asyncio.gather(*self._active, return_exceptions=True)
                self._active.clear()

    async def run_until_idle(self) -> None:
        while True:
            claimed = await self._fill_capacity()
            if not self._active:
                if not claimed:
                    return
                continue
            done, _ = await asyncio.wait(self._active, return_when=asyncio.FIRST_COMPLETED)
            self._active.difference_update(done)
            await asyncio.gather(*done)

    def stop(self) -> None:
        self._stopping.set()

    async def _fill_capacity(self) -> bool:
        claimed_any = False
        while len(self._active) < self._settings.worker_concurrency:
            job = await asyncio.to_thread(
                self._compaction_jobs.claim_next,
                self._settings.worker_id,
                lease_seconds=max(10, int(self._settings.lease_seconds)),
            )
            if job is None and self._memory_jobs is not None:
                job = await asyncio.to_thread(
                    self._memory_jobs.claim_next_memory_job,
                    self._settings.worker_id,
                    lease_seconds=max(10, int(self._settings.lease_seconds)),
                )
            if job is None:
                break
            claimed_any = True
            self._active.add(asyncio.create_task(self._execute(job)))
        return claimed_any

    async def _execute(self, job: CompactionJobRecord | CareerMemoryJobRecord) -> None:
        try:
            if isinstance(job, CareerMemoryJobRecord):
                await asyncio.to_thread(self._processor.process_memory, job)
                if self._memory_jobs is not None:
                    self._memory_jobs.finish_memory_job(
                        job.id, self._settings.worker_id, status="succeeded"
                    )
            else:
                await asyncio.to_thread(self._processor.process_compaction, job)
        except Exception:
            LOGGER.exception("记忆 Worker 执行失败：job_id=%s", job.id)
            if isinstance(job, CareerMemoryJobRecord) and self._memory_jobs is not None:
                self._memory_jobs.finish_memory_job(
                    job.id,
                    self._settings.worker_id,
                    status="failed",
                    error_code="memory_extraction_failed",
                )
            else:
                self._compaction_jobs.finish(
                    job.id,
                    self._settings.worker_id,
                    status="failed",
                    error_code="memory_worker_execution_failed",
                )
