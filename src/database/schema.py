from __future__ import annotations


SCHEMA_VERSION = 1
SCHEMA_NAME = "initial_schema"


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS repositories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        github_id INTEGER NOT NULL UNIQUE,
        owner TEXT NOT NULL,
        name TEXT NOT NULL,
        full_name TEXT NOT NULL UNIQUE,
        html_url TEXT NOT NULL,
        description TEXT,
        language TEXT,
        stars INTEGER NOT NULL DEFAULT 0 CHECK (stars >= 0),
        forks INTEGER NOT NULL DEFAULT 0 CHECK (forks >= 0),
        open_issues INTEGER NOT NULL DEFAULT 0 CHECK (open_issues >= 0),
        default_branch TEXT,
        pushed_at TEXT,
        github_updated_at TEXT,
        github_created_at TEXT,
        is_archived INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
        is_fork INTEGER NOT NULL DEFAULT 0 CHECK (is_fork IN (0, 1)),
        first_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        last_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS star_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        repository_id INTEGER NOT NULL,
        snapshot_date TEXT NOT NULL,
        stars INTEGER NOT NULL CHECK (stars >= 0),
        captured_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        FOREIGN KEY (repository_id) REFERENCES repositories(id) ON DELETE CASCADE,
        UNIQUE (repository_id, snapshot_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS weekly_rankings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        week_start TEXT NOT NULL,
        week_end TEXT NOT NULL,
        repository_id INTEGER NOT NULL,
        rank INTEGER NOT NULL CHECK (rank > 0),
        current_stars INTEGER NOT NULL CHECK (current_stars >= 0),
        star_growth INTEGER NOT NULL,
        growth_rate REAL NOT NULL,
        score REAL NOT NULL,
        reason TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        FOREIGN KEY (repository_id) REFERENCES repositories(id) ON DELETE CASCADE,
        UNIQUE (week_end, repository_id),
        UNIQUE (week_end, rank)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS generated_contents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        week_end TEXT NOT NULL,
        title TEXT NOT NULL,
        digest TEXT,
        article_markdown TEXT,
        video_script TEXT,
        voiceover_text TEXT,
        image_prompts_json TEXT,
        raw_response_json TEXT,
        status TEXT NOT NULL DEFAULT 'created',
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS media_assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content_id INTEGER,
        repository_id INTEGER,
        asset_type TEXT NOT NULL,
        provider TEXT,
        path TEXT NOT NULL,
        mime_type TEXT,
        status TEXT NOT NULL DEFAULT 'created',
        metadata_json TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        FOREIGN KEY (content_id) REFERENCES generated_contents(id) ON DELETE SET NULL,
        FOREIGN KEY (repository_id) REFERENCES repositories(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS article_layouts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content_id INTEGER NOT NULL UNIQUE,
        title TEXT NOT NULL,
        digest TEXT,
        article_html TEXT NOT NULL,
        cover_asset_id INTEGER,
        payload_json TEXT,
        status TEXT NOT NULL DEFAULT 'created',
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        FOREIGN KEY (content_id) REFERENCES generated_contents(id) ON DELETE CASCADE,
        FOREIGN KEY (cover_asset_id) REFERENCES media_assets(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS video_storyboards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content_id INTEGER NOT NULL UNIQUE,
        title TEXT NOT NULL,
        progressive_script TEXT NOT NULL,
        seedance_prompt TEXT NOT NULL,
        architecture_image_prompts_json TEXT,
        storyboard_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'ready',
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        FOREIGN KEY (content_id) REFERENCES generated_contents(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS video_clip_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content_id INTEGER NOT NULL,
        storyboard_id INTEGER NOT NULL,
        clip_index INTEGER NOT NULL CHECK (clip_index > 0),
        source_scene_index INTEGER NOT NULL CHECK (source_scene_index > 0),
        clip_title TEXT NOT NULL,
        repository_full_name TEXT,
        planned_duration_seconds INTEGER NOT NULL CHECK (planned_duration_seconds > 0),
        output_start_second INTEGER NOT NULL CHECK (output_start_second >= 0),
        output_end_second INTEGER NOT NULL CHECK (output_end_second > output_start_second),
        narration TEXT NOT NULL,
        subtitle TEXT,
        visual_design TEXT NOT NULL,
        motion_design TEXT NOT NULL,
        transition_to_next TEXT,
        seedance_prompt TEXT NOT NULL,
        reference_image_asset_ids_json TEXT,
        provider TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'planned',
        metadata_json TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        FOREIGN KEY (content_id) REFERENCES generated_contents(id) ON DELETE CASCADE,
        FOREIGN KEY (storyboard_id) REFERENCES video_storyboards(id) ON DELETE CASCADE,
        UNIQUE (storyboard_id, clip_index)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS draft_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        layout_id INTEGER NOT NULL UNIQUE,
        wechat_draft_id TEXT,
        wechat_media_id TEXT,
        status TEXT NOT NULL DEFAULT 'created',
        response_json TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        FOREIGN KEY (layout_id) REFERENCES article_layouts(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS approval_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        draft_id INTEGER NOT NULL,
        decision TEXT NOT NULL,
        operator TEXT,
        comment TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        FOREIGN KEY (draft_id) REFERENCES draft_records(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS content_approval_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content_id INTEGER NOT NULL,
        decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected', 'regenerate_requested')),
        operator TEXT,
        comment TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        FOREIGN KEY (content_id) REFERENCES generated_contents(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS publish_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        draft_id INTEGER NOT NULL UNIQUE,
        wechat_publish_id TEXT,
        status TEXT NOT NULL DEFAULT 'created',
        published_at TEXT,
        response_json TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        FOREIGN KEY (draft_id) REFERENCES draft_records(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS task_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL UNIQUE,
        task_name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'created',
        started_at TEXT,
        finished_at TEXT,
        retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
        error_message TEXT,
        metadata_json TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS error_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_run_id INTEGER,
        task_name TEXT,
        severity TEXT NOT NULL,
        message TEXT NOT NULL,
        error_type TEXT,
        stack_trace TEXT,
        analysis TEXT,
        metadata_json TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        resolved_at TEXT,
        FOREIGN KEY (task_run_id) REFERENCES task_runs(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_repositories_full_name
        ON repositories(full_name)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_star_snapshots_date
        ON star_snapshots(snapshot_date)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_weekly_rankings_week_end
        ON weekly_rankings(week_end)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_task_runs_task_name
        ON task_runs(task_name)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_error_events_created_at
        ON error_events(created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_content_approval_records_content_id
        ON content_approval_records(content_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_video_storyboards_content_id
        ON video_storyboards(content_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_video_clip_plans_content_id
        ON video_clip_plans(content_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_video_clip_plans_storyboard_id
        ON video_clip_plans(storyboard_id)
    """,
]
