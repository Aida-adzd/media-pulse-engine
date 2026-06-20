-- Add 'instagram' as a valid content_type in sources table
ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_content_type_check;
ALTER TABLE sources ADD CONSTRAINT sources_content_type_check
  CHECK (content_type IN ('youtube', 'article', 'podcast', 'pdf', 'tweet', 'instagram', 'other'));
