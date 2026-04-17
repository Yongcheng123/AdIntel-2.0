ALTER TABLE IF EXISTS socialpeta_creatives
  ADD COLUMN IF NOT EXISTS target_query VARCHAR(255),
  ADD COLUMN IF NOT EXISTS advertiser_identifier VARCHAR(255),
  ADD COLUMN IF NOT EXISTS page_name VARCHAR(255),
  ADD COLUMN IF NOT EXISTS body TEXT,
  ADD COLUMN IF NOT EXISTS message TEXT,
  ADD COLUMN IF NOT EXISTS call_to_action VARCHAR(255),
  ADD COLUMN IF NOT EXISTS ads_type INTEGER,
  ADD COLUMN IF NOT EXISTS preview_image_url TEXT,
  ADD COLUMN IF NOT EXISTS resource_urls JSONB,
  ADD COLUMN IF NOT EXISTS impression BIGINT,
  ADD COLUMN IF NOT EXISTS popularity INTEGER,
  ADD COLUMN IF NOT EXISTS creative_score INTEGER,
  ADD COLUMN IF NOT EXISTS created_at_platform TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS has_page_id BOOLEAN,
  ADD COLUMN IF NOT EXISTS has_store_url BOOLEAN,
  ADD COLUMN IF NOT EXISTS is_page_analysis BOOLEAN;

CREATE INDEX IF NOT EXISTS idx_socialpeta_creatives_target_query ON socialpeta_creatives(target_query);
CREATE INDEX IF NOT EXISTS idx_socialpeta_creatives_advertiser_identifier ON socialpeta_creatives(advertiser_identifier);
