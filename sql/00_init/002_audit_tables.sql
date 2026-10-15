-- Audit tables. Never dropped by the pipeline so that history survives re-runs.
CREATE TABLE IF NOT EXISTS audit.pipeline_run (
    run_id        bigserial PRIMARY KEY,
    run_type      text        NOT NULL,           -- ingest | staging | analytics | train | mart | quality ...
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    status        text        NOT NULL DEFAULT 'running',  -- running | success | failed
    data_dir      text,
    details       jsonb
);

CREATE TABLE IF NOT EXISTS audit.table_load_log (
    load_id       bigserial PRIMARY KEY,
    run_id        bigint REFERENCES audit.pipeline_run(run_id),
    table_name    text        NOT NULL,
    source_file   text,
    source_bytes  bigint,
    csv_rows      bigint,
    db_rows       bigint,
    column_count  integer,
    status        text        NOT NULL,           -- loaded | skipped_unchanged | failed
    duration_s    numeric(12,2),
    loaded_at     timestamptz NOT NULL DEFAULT now(),
    details       jsonb
);

CREATE TABLE IF NOT EXISTS audit.data_quality_result (
    result_id     bigserial PRIMARY KEY,
    run_id        bigint REFERENCES audit.pipeline_run(run_id),
    layer         text        NOT NULL,           -- raw | staging | analytics | mart | model
    check_name    text        NOT NULL,
    table_name    text,
    column_name   text,
    status        text        NOT NULL,           -- pass | warn | fail
    observed_value double precision,
    expected_rule text,
    checked_at    timestamptz NOT NULL DEFAULT now(),
    details       jsonb
);
CREATE INDEX IF NOT EXISTS ix_dq_run ON audit.data_quality_result(run_id);

CREATE TABLE IF NOT EXISTS audit.model_registry (
    model_version     text PRIMARY KEY,
    algorithm         text        NOT NULL,
    trained_at        timestamptz NOT NULL DEFAULT now(),
    feature_version   text        NOT NULL,
    feature_count     integer,
    feature_schema_hash text      NOT NULL,
    calibration_method text,
    split_seed        integer,
    data_fingerprint  text,
    selected_threshold double precision,
    is_champion       boolean     NOT NULL DEFAULT false,
    artifact_path     text,
    params            jsonb,
    metrics           jsonb
);
