-- Separate database for the backend test suite. Tests drop and recreate its
-- schema on every run, so it must never be the development database.
CREATE DATABASE supportiq_test;
