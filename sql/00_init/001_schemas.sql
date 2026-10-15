-- Layered schemas (spec §6.1). Idempotent: safe to re-run.
CREATE SCHEMA IF NOT EXISTS raw;        -- CSV preserved as-is, minimal typing
CREATE SCHEMA IF NOT EXISTS staging;    -- cleaning, standard derivations, intermediate aggregates
CREATE SCHEMA IF NOT EXISTS analytics;  -- customer feature mart, model inputs/outputs
CREATE SCHEMA IF NOT EXISTS mart;       -- Power BI star schema + KPI views
CREATE SCHEMA IF NOT EXISTS audit;      -- run history, load logs, data quality, model registry
