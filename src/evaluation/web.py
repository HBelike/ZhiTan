"""评测中心管理员 API。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from threading import Lock
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from src.career_assistant.persistence.database import CareerDatabase
from src.evaluation.contracts import EvaluationRunStatus, EvaluationSourceKind, EvaluationTargetType
from src.evaluation.repository import EvaluationRepository
from src.evaluation.runners import InterviewRagExperimentRunner
from src.evaluation.service import EvaluationService
from src.platform_access.contracts import PlatformUser
from src.platform_access.web import require_admin


router = APIRouter(prefix="/api/evaluations", tags=["evaluation-center"])


@dataclass
class EvaluationRuntime:
    database: CareerDatabase
    repository: EvaluationRepository
    service: EvaluationService

    def close(self) -> None:
        self.database.close()


class CreateDatasetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    target_type: EvaluationTargetType
    description: str = Field(default="", max_length=1_000)


class CreateDatasetVersionRequest(BaseModel):
    version_label: str = Field(min_length=1, max_length=60)


class CreateCaseRequest(BaseModel):
    case_key: str = Field(min_length=1, max_length=100)
    source_kind: EvaluationSourceKind
    source_ref_id: str | None = Field(default=None, max_length=200)
    input: dict[str, Any]
    expectation: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateExperimentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    target_type: EvaluationTargetType
    dataset_version_id: UUID
    baseline_experiment_id: UUID | None = None
    git_commit: str | None = Field(default=None, max_length=64)
    prompt_version: str | None = Field(default=None, max_length=80)
    model_provider: str | None = Field(default=None, max_length=80)
    model_id: str | None = Field(default=None, max_length=200)
    retrieval_strategy: str | None = Field(default=None, max_length=100)
    config: dict[str, Any] = Field(default_factory=dict)
    repetitions: int = Field(default=1, ge=1, le=20)


class RecordRunRequest(BaseModel):
    case_id: UUID
    repetition: int = Field(default=1, ge=1, le=20)
    status: EvaluationRunStatus
    output: dict[str, Any] = Field(default_factory=dict)
    observation: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = Field(default=None, max_length=300)
    latency_ms: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    error_code: str | None = Field(default=None, max_length=100)


def install_evaluation_api(app: FastAPI) -> None:
    """安装独立评测 API，并在首次请求时连接 PostgreSQL。"""

    app.include_router(router)
    app.state.evaluation_runtime = None
    app.state.evaluation_runtime_lock = Lock()

    @app.on_event("shutdown")
    def close_evaluation_runtime() -> None:
        runtime: EvaluationRuntime | None = app.state.evaluation_runtime
        if runtime is not None:
            runtime.close()
            app.state.evaluation_runtime = None


def get_evaluation_runtime(request: Request) -> EvaluationRuntime:
    existing: EvaluationRuntime | None = request.app.state.evaluation_runtime
    if existing is not None:
        return existing
    lock: Lock = request.app.state.evaluation_runtime_lock
    with lock:
        existing = request.app.state.evaluation_runtime
        if existing is not None:
            return existing
        database_url = os.getenv("CAREER_DATABASE_URL", "").strip()
        if not database_url:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="评测中心数据库尚未配置 CAREER_DATABASE_URL",
            )
        try:
            database = CareerDatabase(database_url)
            repository = EvaluationRepository(database)
            runtime = EvaluationRuntime(
                database=database,
                repository=repository,
                service=EvaluationService(repository),
            )
        except (OSError, SQLAlchemyError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"评测中心初始化失败：{exc}",
            ) from exc
        request.app.state.evaluation_runtime = runtime
        return runtime


@router.get("/overview")
def get_overview(
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, Any]:
    return _execute(lambda: get_evaluation_runtime(request).repository.get_overview(user.organization_id))


@router.get("/datasets")
def list_datasets(
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, Any]:
    items = _execute(lambda: get_evaluation_runtime(request).repository.list_datasets(user.organization_id))
    return {"items": items}


@router.post("/datasets", status_code=status.HTTP_201_CREATED)
def create_dataset(
    payload: CreateDatasetRequest,
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, Any]:
    item = _execute(
        lambda: get_evaluation_runtime(request).service.create_dataset(
            organization_id=user.organization_id,
            created_by=user.id,
            name=payload.name,
            target_type=payload.target_type,
            description=payload.description,
        )
    )
    return {"item": item}


@router.post("/datasets/{dataset_id}/versions", status_code=status.HTTP_201_CREATED)
def create_dataset_version(
    dataset_id: UUID,
    payload: CreateDatasetVersionRequest,
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, Any]:
    item = _execute(
        lambda: get_evaluation_runtime(request).service.create_dataset_version(
            organization_id=user.organization_id,
            dataset_id=dataset_id,
            created_by=user.id,
            version_label=payload.version_label,
        )
    )
    return {"item": item}


@router.get("/dataset-versions/{version_id}/cases")
def list_cases(
    version_id: UUID,
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, Any]:
    items = _execute(
        lambda: get_evaluation_runtime(request).repository.list_cases(user.organization_id, version_id)
    )
    return {"items": items}


@router.post("/dataset-versions/{version_id}/cases", status_code=status.HTTP_201_CREATED)
def add_case(
    version_id: UUID,
    payload: CreateCaseRequest,
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, Any]:
    item = _execute(
        lambda: get_evaluation_runtime(request).service.add_case(
            organization_id=user.organization_id,
            dataset_version_id=version_id,
            case_key=payload.case_key,
            source_kind=payload.source_kind,
            source_ref_id=payload.source_ref_id,
            input_data=payload.input,
            expectation=payload.expectation,
            metadata=payload.metadata,
        )
    )
    return {"item": item}


@router.post("/dataset-versions/{version_id}/freeze")
def freeze_dataset_version(
    version_id: UUID,
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, Any]:
    item = _execute(
        lambda: get_evaluation_runtime(request).service.freeze_dataset_version(
            organization_id=user.organization_id,
            dataset_version_id=version_id,
        )
    )
    return {"item": item}


@router.get("/metric-definitions")
def list_metric_definitions(
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, Any]:
    del user
    items = _execute(lambda: get_evaluation_runtime(request).repository.list_metric_definitions())
    return {"items": items}


@router.get("/runtime-config/interview-rag")
def get_interview_rag_runtime_config(
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, Any]:
    """返回不含凭证的当前 RAG 配置，创建实验时直接固化该快照。"""

    del user
    from src.career_assistant.web.router import get_career_services

    config = get_career_services(request).interview_retrieval_service.configuration_snapshot
    return {"config": config}


@router.get("/experiments")
def list_experiments(
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, Any]:
    items = _execute(
        lambda: get_evaluation_runtime(request).repository.list_experiments(user.organization_id)
    )
    return {"items": items}


@router.post("/experiments", status_code=status.HTTP_201_CREATED)
def create_experiment(
    payload: CreateExperimentRequest,
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, Any]:
    item = _execute(
        lambda: get_evaluation_runtime(request).service.create_experiment(
            organization_id=user.organization_id,
            created_by=user.id,
            name=payload.name,
            target_type=payload.target_type,
            dataset_version_id=payload.dataset_version_id,
            baseline_experiment_id=payload.baseline_experiment_id,
            git_commit=payload.git_commit,
            prompt_version=payload.prompt_version,
            model_provider=payload.model_provider,
            model_id=payload.model_id,
            retrieval_strategy=payload.retrieval_strategy,
            config=payload.config,
            repetitions=payload.repetitions,
        )
    )
    return {"item": item}


@router.get("/experiments/{experiment_id}")
def get_experiment(
    experiment_id: UUID,
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, Any]:
    item = _execute(
        lambda: get_evaluation_runtime(request).repository.get_experiment(
            user.organization_id,
            experiment_id,
        )
    )
    return {"item": item}


@router.post("/experiments/{experiment_id}/runs", status_code=status.HTTP_201_CREATED)
def record_run(
    experiment_id: UUID,
    payload: RecordRunRequest,
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, Any]:
    item = _execute(
        lambda: get_evaluation_runtime(request).service.record_run(
            organization_id=user.organization_id,
            experiment_id=experiment_id,
            case_id=payload.case_id,
            repetition=payload.repetition,
            status=payload.status,
            output=payload.output,
            observation=payload.observation,
            trace_id=payload.trace_id,
            latency_ms=payload.latency_ms,
            input_tokens=payload.input_tokens,
            output_tokens=payload.output_tokens,
            error_code=payload.error_code,
        )
    )
    return {"item": item}


@router.post("/experiments/{experiment_id}/complete")
def complete_experiment(
    experiment_id: UUID,
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, Any]:
    item = _execute(
        lambda: get_evaluation_runtime(request).repository.complete_experiment(
            user.organization_id,
            experiment_id,
        )
    )
    return {"item": item}


@router.post("/experiments/{experiment_id}/execute-interview-rag")
def execute_interview_rag_experiment(
    experiment_id: UUID,
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, Any]:
    """用当前项目的真实面经检索链路执行冻结 RAG 案例。"""

    # 延迟导入避免评测模块初始化时反向触发完整 Career 服务。
    from src.career_assistant.web.router import get_career_services

    runtime = get_evaluation_runtime(request)
    retrieval_service = get_career_services(request).interview_retrieval_service
    runner = InterviewRagExperimentRunner(
        runtime.repository,
        runtime.service,
        retrieval_service,
    )
    return _execute(lambda: runner.run(user.organization_id, experiment_id))


def _execute(handler: Any) -> Any:
    try:
        return handler()
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="名称、版本或运行记录已经存在") from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="评测数据库不可用，请确认已执行最新迁移") from exc
