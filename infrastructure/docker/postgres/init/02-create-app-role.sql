-- Least-privilege role the API connects as (DATABASE_URL).
--
-- It owns nothing and cannot bypass row-level security, so tenant isolation
-- policies apply to every query the application makes. Table privileges are
-- granted by the Alembic migrations, which run as the owner role
-- (MIGRATION_DATABASE_URL). Development password only.
CREATE ROLE supportiq_app LOGIN PASSWORD 'supportiq_app'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
