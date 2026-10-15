-- MANUAL USE ONLY. Drops every project schema including audit history.
-- psql -f sql/99_teardown/999_drop_all.sql
DROP SCHEMA IF EXISTS mart CASCADE;
DROP SCHEMA IF EXISTS analytics CASCADE;
DROP SCHEMA IF EXISTS staging CASCADE;
DROP SCHEMA IF EXISTS raw CASCADE;
DROP SCHEMA IF EXISTS audit CASCADE;
