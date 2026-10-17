-- Runs once, when the Postgres container is first created.
-- The main database (opsdesk) is created from POSTGRES_DB; this adds a separate one for tests.
CREATE DATABASE opsdesk_test;
