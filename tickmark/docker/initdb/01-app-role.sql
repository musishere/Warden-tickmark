-- Local dev only: the login role the Tickmark app connects as. It is not the table owner and not
-- a superuser, so row-level security applies to it and it cannot alter tables or drop triggers.
-- Migrations grant it SELECT + INSERT only. In production create it with a secret password.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tickmark_app') THEN
        CREATE ROLE tickmark_app LOGIN PASSWORD 'tickmark_app' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    ELSE
        ALTER ROLE tickmark_app LOGIN PASSWORD 'tickmark_app' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END $$;
