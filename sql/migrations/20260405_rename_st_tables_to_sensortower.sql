-- Rename legacy SensorTower tables from st_* to sensortower_* while preserving data.

ALTER TABLE IF EXISTS st_downloads RENAME TO sensortower_downloads;
ALTER TABLE IF EXISTS st_usage RENAME TO sensortower_usage;
ALTER TABLE IF EXISTS st_retention RENAME TO sensortower_retention;
ALTER TABLE IF EXISTS st_impression_share RENAME TO sensortower_impression_share;
ALTER TABLE IF EXISTS st_demographics RENAME TO sensortower_demographics;
ALTER TABLE IF EXISTS st_rankings RENAME TO sensortower_rankings;
ALTER TABLE IF EXISTS st_reviews RENAME TO sensortower_reviews;
ALTER TABLE IF EXISTS st_review_texts RENAME TO sensortower_review_texts;
ALTER TABLE IF EXISTS st_creatives RENAME TO sensortower_creatives;
ALTER TABLE IF EXISTS st_aso_keywords RENAME TO sensortower_aso_keywords;

ALTER INDEX IF EXISTS idx_st_downloads_advertiser_name RENAME TO idx_sensortower_downloads_advertiser_name;
ALTER INDEX IF EXISTS idx_st_usage_advertiser_name RENAME TO idx_sensortower_usage_advertiser_name;
ALTER INDEX IF EXISTS idx_st_retention_advertiser_name RENAME TO idx_sensortower_retention_advertiser_name;
ALTER INDEX IF EXISTS idx_st_impression_share_advertiser_name RENAME TO idx_sensortower_impression_share_advertiser_name;
ALTER INDEX IF EXISTS idx_st_demographics_advertiser_name RENAME TO idx_sensortower_demographics_advertiser_name;
ALTER INDEX IF EXISTS idx_st_rankings_advertiser_name RENAME TO idx_sensortower_rankings_advertiser_name;
ALTER INDEX IF EXISTS idx_st_reviews_advertiser_name RENAME TO idx_sensortower_reviews_advertiser_name;
ALTER INDEX IF EXISTS idx_st_review_texts_advertiser_name RENAME TO idx_sensortower_review_texts_advertiser_name;
ALTER INDEX IF EXISTS idx_st_creatives_advertiser_name RENAME TO idx_sensortower_creatives_advertiser_name;
ALTER INDEX IF EXISTS idx_st_aso_keywords_advertiser_name RENAME TO idx_sensortower_aso_keywords_advertiser_name;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_st_downloads') THEN
    ALTER TABLE sensortower_downloads RENAME CONSTRAINT uq_st_downloads TO uq_sensortower_downloads;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_st_usage') THEN
    ALTER TABLE sensortower_usage RENAME CONSTRAINT uq_st_usage TO uq_sensortower_usage;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_st_retention') THEN
    ALTER TABLE sensortower_retention RENAME CONSTRAINT uq_st_retention TO uq_sensortower_retention;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_st_impression_share') THEN
    ALTER TABLE sensortower_impression_share RENAME CONSTRAINT uq_st_impression_share TO uq_sensortower_impression_share;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_st_demographics') THEN
    ALTER TABLE sensortower_demographics RENAME CONSTRAINT uq_st_demographics TO uq_sensortower_demographics;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_st_rankings') THEN
    ALTER TABLE sensortower_rankings RENAME CONSTRAINT uq_st_rankings TO uq_sensortower_rankings;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_st_reviews') THEN
    ALTER TABLE sensortower_reviews RENAME CONSTRAINT uq_st_reviews TO uq_sensortower_reviews;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_st_review_texts') THEN
    ALTER TABLE sensortower_review_texts RENAME CONSTRAINT uq_st_review_texts TO uq_sensortower_review_texts;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_st_creatives') THEN
    ALTER TABLE sensortower_creatives RENAME CONSTRAINT uq_st_creatives TO uq_sensortower_creatives;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_st_aso_keywords') THEN
    ALTER TABLE sensortower_aso_keywords RENAME CONSTRAINT uq_st_aso_keywords TO uq_sensortower_aso_keywords;
  END IF;
END $$;
