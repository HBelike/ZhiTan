"""新增版本化评测数据、实验、运行与指标得分。"""

from __future__ import annotations

from alembic import op


revision = "20260820_12"
down_revision = "20260817_11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建独立 evaluation schema，不改写业务运行记录。"""

    op.execute("CREATE SCHEMA IF NOT EXISTS evaluation")
    op.execute(
        """
        CREATE TABLE evaluation.datasets (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            target_type TEXT NOT NULL CHECK (
                target_type IN ('interview_rag', 'career_agent', 'resume_optimizer')
            ),
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
            created_by UUID
                REFERENCES career_assistant.platform_users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (organization_id, name)
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE evaluation.dataset_versions (
            id UUID PRIMARY KEY,
            dataset_id UUID NOT NULL REFERENCES evaluation.datasets(id) ON DELETE CASCADE,
            version_label TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'frozen')),
            case_count INTEGER NOT NULL DEFAULT 0 CHECK (case_count >= 0),
            content_hash CHAR(64),
            created_by UUID
                REFERENCES career_assistant.platform_users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            frozen_at TIMESTAMPTZ,
            UNIQUE (dataset_id, version_label)
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE evaluation.cases (
            id UUID PRIMARY KEY,
            dataset_version_id UUID NOT NULL
                REFERENCES evaluation.dataset_versions(id) ON DELETE CASCADE,
            case_key TEXT NOT NULL,
            source_kind TEXT NOT NULL CHECK (
                source_kind IN ('anonymized_real', 'failure_replay', 'curated_edge', 'synthetic')
            ),
            source_ref_id TEXT,
            input_json JSONB NOT NULL,
            expectation_json JSONB NOT NULL,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            content_hash CHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (dataset_version_id, case_key)
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE evaluation.experiments (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            target_type TEXT NOT NULL CHECK (
                target_type IN ('interview_rag', 'career_agent', 'resume_optimizer')
            ),
            dataset_version_id UUID NOT NULL
                REFERENCES evaluation.dataset_versions(id) ON DELETE RESTRICT,
            baseline_experiment_id UUID REFERENCES evaluation.experiments(id) ON DELETE SET NULL,
            status TEXT NOT NULL DEFAULT 'draft' CHECK (
                status IN ('draft', 'running', 'completed', 'failed')
            ),
            git_commit TEXT,
            prompt_version TEXT,
            model_provider TEXT,
            model_id TEXT,
            retrieval_strategy TEXT,
            config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            config_hash CHAR(64) NOT NULL,
            repetitions INTEGER NOT NULL DEFAULT 1 CHECK (repetitions BETWEEN 1 AND 20),
            created_by UUID
                REFERENCES career_assistant.platform_users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            UNIQUE (organization_id, name)
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE evaluation.runs (
            id UUID PRIMARY KEY,
            experiment_id UUID NOT NULL REFERENCES evaluation.experiments(id) ON DELETE CASCADE,
            case_id UUID NOT NULL REFERENCES evaluation.cases(id) ON DELETE RESTRICT,
            repetition INTEGER NOT NULL DEFAULT 1 CHECK (repetition > 0),
            status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'timeout')),
            output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            observation_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            trace_id TEXT,
            latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
            input_tokens INTEGER CHECK (input_tokens >= 0),
            output_tokens INTEGER CHECK (output_tokens >= 0),
            error_code TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (experiment_id, case_id, repetition)
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE evaluation.metric_definitions (
            metric_key TEXT NOT NULL,
            version TEXT NOT NULL,
            display_name TEXT NOT NULL,
            target_type TEXT NOT NULL,
            evaluator_kind TEXT NOT NULL CHECK (
                evaluator_kind IN ('deterministic', 'llm_judge', 'human')
            ),
            direction TEXT NOT NULL CHECK (
                direction IN ('higher_is_better', 'lower_is_better')
            ),
            unit TEXT NOT NULL,
            aggregation TEXT NOT NULL,
            formula TEXT NOT NULL,
            threshold DOUBLE PRECISION NOT NULL,
            critical BOOLEAN NOT NULL DEFAULT FALSE,
            description TEXT NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (metric_key, version)
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE evaluation.scores (
            id UUID PRIMARY KEY,
            run_id UUID NOT NULL REFERENCES evaluation.runs(id) ON DELETE CASCADE,
            metric_key TEXT NOT NULL,
            metric_version TEXT NOT NULL,
            raw_value DOUBLE PRECISION NOT NULL,
            passed BOOLEAN NOT NULL,
            evaluator_kind TEXT NOT NULL CHECK (
                evaluator_kind IN ('deterministic', 'llm_judge', 'human')
            ),
            evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            FOREIGN KEY (metric_key, metric_version)
                REFERENCES evaluation.metric_definitions(metric_key, version) ON DELETE RESTRICT,
            UNIQUE (run_id, metric_key, metric_version, evaluator_kind)
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE evaluation.judgments (
            id UUID PRIMARY KEY,
            run_id UUID NOT NULL REFERENCES evaluation.runs(id) ON DELETE CASCADE,
            metric_key TEXT NOT NULL,
            metric_version TEXT NOT NULL,
            judge_kind TEXT NOT NULL CHECK (judge_kind IN ('llm_judge', 'human')),
            judge_identity TEXT NOT NULL,
            score DOUBLE PRECISION NOT NULL,
            reason TEXT NOT NULL,
            evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            review_status TEXT NOT NULL DEFAULT 'pending' CHECK (
                review_status IN ('pending', 'accepted', 'disputed')
            ),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            FOREIGN KEY (metric_key, metric_version)
                REFERENCES evaluation.metric_definitions(metric_key, version) ON DELETE RESTRICT
        )
        """,
    )

    op.execute("CREATE INDEX idx_eval_cases_version_source ON evaluation.cases (dataset_version_id, source_kind)")
    op.execute("CREATE INDEX idx_eval_experiments_org_created ON evaluation.experiments (organization_id, created_at DESC)")
    op.execute("CREATE INDEX idx_eval_runs_experiment_status ON evaluation.runs (experiment_id, status)")
    op.execute("CREATE INDEX idx_eval_scores_run_passed ON evaluation.scores (run_id, passed)")

    definitions = (
        ("system.success", "1.0", "运行成功率", "all", "deterministic", "higher_is_better", "ratio", "mean", "succeeded_runs / all_runs", 0.99, True, "业务目标执行完成且未返回错误。"),
        ("system.structure_valid", "1.0", "结构合法率", "all", "deterministic", "higher_is_better", "ratio", "mean", "valid_structured_outputs / checked_outputs", 1.0, True, "需要结构化输出的运行是否满足约定 Schema。"),
        ("system.latency_ms", "1.0", "端到端耗时", "all", "deterministic", "lower_is_better", "ms", "p95", "completed_at - started_at", 120000.0, False, "从目标执行开始到结果收口的毫秒数。"),
        ("rag.recall_at_6", "1.0", "Recall@6", "interview_rag", "deterministic", "higher_is_better", "ratio", "macro_mean", "|retrieved_top6 ∩ relevant| / |relevant|", 0.85, False, "前六个召回结果覆盖金标相关 Chunk 的比例。"),
        ("rag.mrr", "1.0", "MRR", "interview_rag", "deterministic", "higher_is_better", "ratio", "mean", "mean(1 / first_relevant_rank)", 0.80, False, "第一个相关 Chunk 出现位置的倒数。"),
        ("rag.ndcg_at_6", "1.0", "nDCG@6", "interview_rag", "deterministic", "higher_is_better", "ratio", "macro_mean", "DCG@6 / IDCG@6", 0.75, False, "相关证据在前六个结果中的排序质量。"),
        ("agent.required_step_coverage", "1.0", "必要步骤覆盖率", "career_agent", "deterministic", "higher_is_better", "ratio", "macro_mean", "executed_required_steps / required_steps", 0.95, False, "Agent 实际执行路径覆盖期望必要步骤的比例。"),
        ("agent.unnecessary_step_rate", "1.0", "不必要步骤率", "career_agent", "deterministic", "lower_is_better", "ratio", "macro_mean", "steps_outside_allowed_set / executed_steps", 0.10, False, "实际轨迹中不属于允许步骤集合的比例。"),
        ("resume.forbidden_claim_match_rate", "1.0", "禁止事实命中率", "resume_optimizer", "deterministic", "lower_is_better", "ratio", "macro_mean", "matched_forbidden_claims / forbidden_claims", 0.0, True, "输出命中样本中明确标记为禁止新增或错误的事实比例。")
    )
    for item in definitions:
        escaped = [str(value).replace("'", "''") for value in item]
        op.execute(
            "INSERT INTO evaluation.metric_definitions ("
            "metric_key, version, display_name, target_type, evaluator_kind, direction, "
            "unit, aggregation, formula, threshold, critical, description) VALUES ("
            f"'{escaped[0]}', '{escaped[1]}', '{escaped[2]}', '{escaped[3]}', "
            f"'{escaped[4]}', '{escaped[5]}', '{escaped[6]}', '{escaped[7]}', "
            f"'{escaped[8]}', {item[9]}, {'TRUE' if item[10] else 'FALSE'}, '{escaped[11]}')"
        )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS evaluation CASCADE")
