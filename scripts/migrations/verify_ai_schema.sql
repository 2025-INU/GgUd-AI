-- Post-migration verification queries

-- Should include places + place_summary_embeddings
\dt

-- image_url should exist
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'places'
  AND column_name = 'image_url';

-- Should return 0 rows
SELECT tablename
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN ('reviews', 'review_embeddings');

