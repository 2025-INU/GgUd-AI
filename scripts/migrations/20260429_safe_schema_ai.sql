-- Safe schema migration for AI server deployment.
-- Idempotent: can be executed multiple times safely.

BEGIN;

-- 1) Required extension
CREATE EXTENSION IF NOT EXISTS vector;

-- 2) places.image_url used by crawler + recommendation response
ALTER TABLE IF EXISTS places
ADD COLUMN IF NOT EXISTS image_url TEXT;

-- 3) Unused tables: remove to avoid drift with current app behavior
DROP TABLE IF EXISTS review_embeddings CASCADE;
DROP TABLE IF EXISTS reviews CASCADE;

COMMIT;

