"""线上笔试助手 FastAPI 路由。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from src.career_assistant.model_clients import ModelInvocationError
from src.career_assistant.online_assessment.archive import (
    AssessmentArchiveItem,
    AssessmentArchiveService,
)
from src.career_assistant.online_assessment.contracts import (
    AssessmentExecutionReport,
    AssessmentProblem,
    AssessmentSolution,
    AssessmentTestCase,
    CapturedProblemInput,
)
from src.career_assistant.online_assessment.execution import (
    ExecutionUnavailableError,
    PistonExecutionProvider,
)
from src.career_assistant.online_assessment.model_output import OnlineAssessmentModelOutputError
from src.career_assistant.online_assessment.solution_service import (
    ConfiguredModelResolver,
    OnlineAssessmentModelUnavailableError,
    OnlineAssessmentService,
)
from src.career_assistant.settings import load_online_assessment_settings
from src.platform_access.contracts import PlatformRole


router = APIRouter(prefix="/api/career/online-assessments", tags=["online-assessment"])


class AnalyzeAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_version: int = Field(ge=1)
    capture: CapturedProblemInput


class SolveAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_version: int = Field(ge=1)
    problem: AssessmentProblem


class ExecuteAssessmentRequest(SolveAssessmentRequest):
    solution: AssessmentSolution
    tests: list[AssessmentTestCase] = Field(default_factory=list, max_length=20)
    auto_repair: bool = True


class ArchiveAssessmentItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    problem: AssessmentProblem
    solution: AssessmentSolution
    report: AssessmentExecutionReport | None = None


class ArchiveAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_name: str = Field(min_length=1, max_length=120)
    role_name: str = Field(min_length=1, max_length=160)
    assessment_date: date | None = None
    items: list[ArchiveAssessmentItemRequest] = Field(min_length=1, max_length=50)


def get_online_assessment_service(request: Request) -> Iterator[OnlineAssessmentService]:
    """按请求 Actor 解析固定模型，执行器客户端在请求结束时释放。"""

    from src.career_assistant.web.router import get_career_services, get_request_actor

    services = get_career_services(request)
    actor = get_request_actor()
    settings = load_online_assessment_settings(
        request.app.state.career_assistant_project_root / "config" / "career_assistant.yaml"
    )
    provider = PistonExecutionProvider(
        settings.piston_base_url,
        request_timeout_seconds=settings.request_timeout_seconds,
    )
    service = OnlineAssessmentService(
        resolver=ConfiguredModelResolver(
            services.model_profile_repository,
            services.model_gateway,
            actor.organization_id,
        ),
        model_client=services.model_connection_client,
        problem_extractor_profile_key=settings.problem_extractor_profile_key,
        answer_profile_key=settings.answer_profile_key,
        execution_provider=provider,
        max_test_cases=settings.max_test_cases,
        max_repair_rounds=settings.max_repair_rounds,
    )
    try:
        yield service
    finally:
        provider.close()


def get_online_assessment_archive_service(request: Request) -> AssessmentArchiveService:
    from src.career_assistant.web.router import get_career_services

    services = get_career_services(request)
    return AssessmentArchiveService(
        services.interview_library_repository,
        services.interview_library_service,
    )


@router.post("/analyze")
def analyze_assessment(
    body: AnalyzeAssessmentRequest,
    service: OnlineAssessmentService = Depends(get_online_assessment_service),
) -> dict[str, object]:
    try:
        problem = service.analyze(body.capture)
    except _MODEL_ERRORS as exc:
        raise _model_http_error(exc) from exc
    return {"run_version": body.run_version, "problem": problem.model_dump(mode="json")}


@router.post("/solve/stream")
def stream_assessment_solution(
    body: SolveAssessmentRequest,
    service: OnlineAssessmentService = Depends(get_online_assessment_service),
) -> StreamingResponse:
    def events():
        yield _ndjson({"type": "phase", "phase": "generating", "run_version": body.run_version})
        try:
            result = service.solve(body.problem)
            if result.solution is None:
                yield _ndjson(
                    {"type": "needs_confirmation", "run_version": body.run_version, "reasons": body.problem.incomplete_reasons}
                )
                return
            yield _ndjson(
                {"type": "solution", "run_version": body.run_version, "solution": result.solution.model_dump(mode="json")}
            )
            yield _ndjson({"type": "done", "run_version": body.run_version})
        except _MODEL_ERRORS as exc:
            yield _ndjson({"type": "error", "run_version": body.run_version, "message": str(exc)})

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/execute")
def execute_assessment(
    body: ExecuteAssessmentRequest,
    service: OnlineAssessmentService = Depends(get_online_assessment_service),
) -> dict[str, object]:
    try:
        tests = tuple(body.tests) or service.generate_tests(body.problem)
        if body.auto_repair:
            result = service.execute_and_repair(body.problem, body.solution, tests)
            solution, report = result.solution, result.report
        else:
            report = service.execute(body.problem, body.solution, tests)
            solution = body.solution
    except ExecutionUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except _MODEL_ERRORS as exc:
        raise _model_http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "run_version": body.run_version,
        "solution": solution.model_dump(mode="json"),
        "tests": [test.model_dump(mode="json") for test in tests],
        "report": report.model_dump(mode="json"),
    }


@router.post("/archive/preview")
def preview_assessment_archive(
    body: ArchiveAssessmentRequest,
    service: AssessmentArchiveService = Depends(get_online_assessment_archive_service),
) -> dict[str, object]:
    try:
        preview = service.preview(
            company_name=body.company_name,
            role_name=body.role_name,
            assessment_date=body.assessment_date,
            items=_archive_items(body),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "markdown": preview.markdown,
        "question_count": preview.question_count,
        "passed_question_count": preview.passed_question_count,
    }


@router.post("/archive", status_code=status.HTTP_201_CREATED)
def archive_assessment(
    body: ArchiveAssessmentRequest,
    request: Request,
    service: AssessmentArchiveService = Depends(get_online_assessment_archive_service),
) -> dict[str, object]:
    from src.career_assistant.web.router import get_request_actor

    actor = get_request_actor()
    try:
        experience = service.archive(
            organization_id=actor.organization_id,
            actor_id=actor.actor_id,
            can_manage_all=actor.role is PlatformRole.ADMIN,
            company_name=body.company_name,
            role_name=body.role_name,
            assessment_date=body.assessment_date,
            items=_archive_items(body),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"experience_id": str(experience.id), "job_name": experience.job_name}


_MODEL_ERRORS = (
    ModelInvocationError,
    OnlineAssessmentModelOutputError,
    OnlineAssessmentModelUnavailableError,
)


def _model_http_error(exc: Exception) -> HTTPException:
    code = 503 if isinstance(exc, (ModelInvocationError, OnlineAssessmentModelUnavailableError)) else 422
    return HTTPException(status_code=code, detail=str(exc))


def _archive_items(body: ArchiveAssessmentRequest) -> tuple[AssessmentArchiveItem, ...]:
    return tuple(AssessmentArchiveItem(item.problem, item.solution, item.report) for item in body.items)


def _ndjson(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
