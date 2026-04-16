-- AdIntel schema
--
-- Keep this file idempotent so it can be auto-applied safely at startup.
--
-- Pattern:
-- 1. Define base tables with CREATE TABLE IF NOT EXISTS.
-- 2. Add indexes with CREATE INDEX IF NOT EXISTS.
-- 3. For changes to existing tables, append additive ALTER TABLE statements
--    at the end of the file using ADD COLUMN IF NOT EXISTS.

-- Base tables
CREATE TABLE IF NOT EXISTS advertisers (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255) NOT NULL UNIQUE,
  domain VARCHAR(255),
  category VARCHAR(255),
  countries_csv VARCHAR(255) NOT NULL DEFAULT 'US',
  sensortower_unified_app_id VARCHAR(255),
  sensortower_publisher_id VARCHAR(255),
  sensortower_ios_app_id VARCHAR(255),
  sensortower_android_package VARCHAR(255),
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scrape_runs (
  id SERIAL PRIMARY KEY,
  advertiser_name VARCHAR(255) NOT NULL,
  platform VARCHAR(64) NOT NULL,
  status VARCHAR(64) NOT NULL DEFAULT 'running',
  message TEXT,
  metadata JSONB,
  started_at TIMESTAMPTZ DEFAULT now(),
  finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_scrape_runs_advertiser_name ON scrape_runs(advertiser_name);
CREATE INDEX IF NOT EXISTS idx_scrape_runs_platform ON scrape_runs(platform);

CREATE TABLE IF NOT EXISTS scrape_run_metrics (
  id SERIAL PRIMARY KEY,
  scrape_run_id INTEGER NOT NULL REFERENCES scrape_runs(id),
  metric_name VARCHAR(50) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  records_written INTEGER NOT NULL DEFAULT 0,
  message TEXT,
  started_at TIMESTAMPTZ DEFAULT now(),
  finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_scrape_run_metrics_run_id ON scrape_run_metrics(scrape_run_id);

CREATE TABLE IF NOT EXISTS requested_advertisers (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255) NOT NULL UNIQUE,
  requested_by VARCHAR(255),
  context TEXT,
  status VARCHAR(64) NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sensortower_downloads (
  id SERIAL PRIMARY KEY,
  advertiser_name VARCHAR(255) NOT NULL,
  period_date DATE NOT NULL,
  granularity VARCHAR(32) NOT NULL DEFAULT 'day',
  country VARCHAR(8) NOT NULL DEFAULT 'US',
  os VARCHAR(32) NOT NULL DEFAULT 'unified',
  downloads INTEGER,
  revenue NUMERIC,
  scraped_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_sensortower_downloads UNIQUE (advertiser_name, period_date, granularity, country, os)
);

CREATE INDEX IF NOT EXISTS idx_sensortower_downloads_advertiser_name ON sensortower_downloads(advertiser_name);

CREATE TABLE IF NOT EXISTS sensortower_usage (
  id SERIAL PRIMARY KEY,
  advertiser_name VARCHAR(255) NOT NULL,
  period_date DATE NOT NULL,
  country VARCHAR(8) NOT NULL DEFAULT 'US',
  avg_dau INTEGER,
  time_spent_min DOUBLE PRECISION,
  sessions_per_day DOUBLE PRECISION,
  scraped_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_sensortower_usage UNIQUE (advertiser_name, period_date, country)
);

CREATE INDEX IF NOT EXISTS idx_sensortower_usage_advertiser_name ON sensortower_usage(advertiser_name);

CREATE TABLE IF NOT EXISTS sensortower_retention (
  id SERIAL PRIMARY KEY,
  advertiser_name VARCHAR(255) NOT NULL,
  cohort_date DATE NOT NULL,
  country VARCHAR(8) NOT NULL DEFAULT 'US',
  d1 DOUBLE PRECISION,
  d3 DOUBLE PRECISION,
  d7 DOUBLE PRECISION,
  d14 DOUBLE PRECISION,
  d30 DOUBLE PRECISION,
  d60 DOUBLE PRECISION,
  scraped_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_sensortower_retention UNIQUE (advertiser_name, cohort_date, country)
);

CREATE INDEX IF NOT EXISTS idx_sensortower_retention_advertiser_name ON sensortower_retention(advertiser_name);

CREATE TABLE IF NOT EXISTS sensortower_impression_share (
  id SERIAL PRIMARY KEY,
  advertiser_name VARCHAR(255) NOT NULL,
  period_date DATE NOT NULL,
  network VARCHAR(128) NOT NULL,
  country VARCHAR(8) NOT NULL DEFAULT 'US',
  sov_pct DOUBLE PRECISION,
  scraped_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_sensortower_impression_share UNIQUE (advertiser_name, period_date, network, country)
);

CREATE INDEX IF NOT EXISTS idx_sensortower_impression_share_advertiser_name ON sensortower_impression_share(advertiser_name);

CREATE TABLE IF NOT EXISTS sensortower_demographics (
  id SERIAL PRIMARY KEY,
  advertiser_name VARCHAR(255) NOT NULL,
  country VARCHAR(8) NOT NULL DEFAULT 'US',
  age_bracket VARCHAR(32) NOT NULL,
  male_pct DOUBLE PRECISION,
  female_pct DOUBLE PRECISION,
  scraped_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_sensortower_demographics UNIQUE (advertiser_name, country, age_bracket)
);

CREATE INDEX IF NOT EXISTS idx_sensortower_demographics_advertiser_name ON sensortower_demographics(advertiser_name);

CREATE TABLE IF NOT EXISTS sensortower_rankings (
  id SERIAL PRIMARY KEY,
  advertiser_name VARCHAR(255) NOT NULL,
  rank_date DATE NOT NULL,
  country VARCHAR(8) NOT NULL DEFAULT 'US',
  category VARCHAR(255) NOT NULL,
  chart_type VARCHAR(64) NOT NULL,
  rank INTEGER,
  is_featured BOOLEAN NOT NULL DEFAULT FALSE,
  scraped_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_sensortower_rankings UNIQUE (advertiser_name, rank_date, country, category, chart_type)
);

CREATE INDEX IF NOT EXISTS idx_sensortower_rankings_advertiser_name ON sensortower_rankings(advertiser_name);

CREATE TABLE IF NOT EXISTS sensortower_reviews (
  id SERIAL PRIMARY KEY,
  advertiser_name VARCHAR(255) NOT NULL,
  period_date DATE NOT NULL,
  country VARCHAR(8) NOT NULL DEFAULT 'US',
  avg_rating DOUBLE PRECISION,
  rating_count INTEGER,
  star_1_count INTEGER,
  star_2_count INTEGER,
  star_3_count INTEGER,
  star_4_count INTEGER,
  star_5_count INTEGER,
  scraped_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_sensortower_reviews UNIQUE (advertiser_name, period_date, country)
);

CREATE INDEX IF NOT EXISTS idx_sensortower_reviews_advertiser_name ON sensortower_reviews(advertiser_name);

CREATE TABLE IF NOT EXISTS sensortower_review_texts (
  id SERIAL PRIMARY KEY,
  advertiser_name VARCHAR(255) NOT NULL,
  review_id BIGINT NOT NULL,
  review_date DATE NOT NULL,
  country VARCHAR(8) NOT NULL DEFAULT 'US',
  star_rating NUMERIC(3, 1),
  username VARCHAR(255),
  title TEXT,
  body TEXT,
  sentiment VARCHAR(64),
  tags JSONB,
  app_version VARCHAR(128),
  os VARCHAR(16) NOT NULL DEFAULT 'ios',
  scraped_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_sensortower_review_texts UNIQUE (advertiser_name, review_id)
);

CREATE INDEX IF NOT EXISTS idx_sensortower_review_texts_advertiser_name ON sensortower_review_texts(advertiser_name);

CREATE TABLE IF NOT EXISTS sensortower_creatives (
  id SERIAL PRIMARY KEY,
  advertiser_name VARCHAR(255) NOT NULL,
  creative_id VARCHAR(255) NOT NULL,
  creative_type VARCHAR(64),
  network VARCHAR(128),
  thumbnail_url TEXT,
  duration_bucket VARCHAR(32),
  first_seen DATE,
  scraped_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_sensortower_creatives UNIQUE (advertiser_name, creative_id)
);

CREATE INDEX IF NOT EXISTS idx_sensortower_creatives_advertiser_name ON sensortower_creatives(advertiser_name);

CREATE TABLE IF NOT EXISTS sensortower_aso_keywords (
  id SERIAL PRIMARY KEY,
  advertiser_name VARCHAR(255) NOT NULL,
  keyword VARCHAR(255) NOT NULL,
  keyword_type VARCHAR(64),
  rank INTEGER,
  traffic_score DOUBLE PRECISION,
  opportunity_score DOUBLE PRECISION,
  country VARCHAR(8) NOT NULL DEFAULT 'US',
  device VARCHAR(32) NOT NULL DEFAULT 'iphone',
  scraped_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_sensortower_aso_keywords UNIQUE (advertiser_name, keyword, country, device)
);

CREATE INDEX IF NOT EXISTS idx_sensortower_aso_keywords_advertiser_name ON sensortower_aso_keywords(advertiser_name);

CREATE TABLE IF NOT EXISTS otterlyai_prompts (
  id SERIAL PRIMARY KEY,
  target_brand_or_domain_name VARCHAR(255) NOT NULL,
  country_code VARCHAR(8) NOT NULL,
  query_window_start_date DATE NOT NULL,
  query_window_end_date DATE NOT NULL,
  prompt_text TEXT NOT NULL,
  prompt_volume INTEGER,
  target_rank INTEGER,
  ai_engine VARCHAR(64) NOT NULL,
  domain_cited BOOLEAN NOT NULL DEFAULT FALSE,
  sentiment_score DOUBLE PRECISION,
  sentiment_label VARCHAR(32),
  competitors JSONB,
  scraped_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_otterlyai_prompts UNIQUE (target_brand_or_domain_name, country_code, ai_engine, prompt_text, query_window_end_date)
);

CREATE INDEX IF NOT EXISTS idx_otterlyai_prompts_target ON otterlyai_prompts(target_brand_or_domain_name);
CREATE INDEX IF NOT EXISTS idx_otterlyai_prompts_country ON otterlyai_prompts(country_code);
CREATE INDEX IF NOT EXISTS idx_otterlyai_prompts_engine ON otterlyai_prompts(ai_engine);

CREATE TABLE IF NOT EXISTS otterlyai_citations (
  id SERIAL PRIMARY KEY,
  target_brand_or_domain_name VARCHAR(255) NOT NULL,
  country_code VARCHAR(8) NOT NULL,
  query_window_start_date DATE NOT NULL,
  query_window_end_date DATE NOT NULL,
  ai_engine VARCHAR(64) NOT NULL,
  cited_url TEXT NOT NULL,
  cited_domain VARCHAR(255),
  citation_count INTEGER,
  brand_mentioned BOOLEAN NOT NULL DEFAULT FALSE,
  domain_category VARCHAR(128),
  competitors JSONB,
  scraped_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_otterlyai_citations UNIQUE (target_brand_or_domain_name, country_code, ai_engine, cited_url, query_window_end_date)
);

CREATE INDEX IF NOT EXISTS idx_otterlyai_citations_target ON otterlyai_citations(target_brand_or_domain_name);
CREATE INDEX IF NOT EXISTS idx_otterlyai_citations_country ON otterlyai_citations(country_code);
CREATE INDEX IF NOT EXISTS idx_otterlyai_citations_engine ON otterlyai_citations(ai_engine);

CREATE TABLE IF NOT EXISTS sensortower_market_top_apps (
  id SERIAL PRIMARY KEY,
  scrape_month DATE NOT NULL,
  country VARCHAR(8) NOT NULL DEFAULT 'US',
  category VARCHAR(255) NOT NULL,
  os VARCHAR(32) NOT NULL DEFAULT 'unified',
  rank INTEGER NOT NULL,
  app_name VARCHAR(255),
  publisher_name VARCHAR(255),
  unified_app_id VARCHAR(255),
  primary_category VARCHAR(255),
  downloads BIGINT,
  revenue NUMERIC,
  dau BIGINT,
  impression_share DOUBLE PRECISION,
  ad_on_admob BOOLEAN DEFAULT FALSE,
  ad_on_facebook BOOLEAN DEFAULT FALSE,
  ad_on_instagram BOOLEAN DEFAULT FALSE,
  ad_on_tiktok BOOLEAN DEFAULT FALSE,
  ad_on_youtube BOOLEAN DEFAULT FALSE,
  ad_on_snapchat BOOLEAN DEFAULT FALSE,
  ad_on_applovin BOOLEAN DEFAULT FALSE,
  ad_on_unity BOOLEAN DEFAULT FALSE,
  ad_on_mintegral BOOLEAN DEFAULT FALSE,
  scraped_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_sensortower_market_top_apps UNIQUE (scrape_month, country, category, os, rank)
);

CREATE INDEX IF NOT EXISTS idx_st_market_top_apps_month ON sensortower_market_top_apps(scrape_month);
CREATE INDEX IF NOT EXISTS idx_st_market_top_apps_category ON sensortower_market_top_apps(category);

-- Additive migrations for existing databases
--
-- When you add a new column to an existing table, append the matching ALTER
-- TABLE statement here. CREATE TABLE IF NOT EXISTS will not modify a table
-- that already exists.
ALTER TABLE scrape_runs
ADD COLUMN IF NOT EXISTS metadata JSONB;

ALTER TABLE advertisers
DROP COLUMN IF EXISTS adclarity_advertiser_id;

ALTER TABLE advertisers
DROP COLUMN IF EXISTS adclarity_brand_id;
