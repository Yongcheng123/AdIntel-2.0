-- Add appfollow_reviews table.
-- Idempotent: safe to run multiple times.

CREATE TABLE IF NOT EXISTS appfollow_reviews (
  id                SERIAL PRIMARY KEY,
  advertiser_name   VARCHAR(255) NOT NULL,
  review_id         VARCHAR(255) NOT NULL,
  review_date       DATE         NOT NULL,
  country           VARCHAR(8)   NOT NULL DEFAULT 'US',
  star_rating       NUMERIC(3,1),
  username          VARCHAR(255),
  title             TEXT,
  body              TEXT,
  sentiment         VARCHAR(64),
  sentiment_score   DOUBLE PRECISION,
  tags              JSONB,
  app_version       VARCHAR(128),
  os                VARCHAR(16)  NOT NULL DEFAULT 'ios',
  appfollow_item_id VARCHAR(255),
  scraped_at        TIMESTAMPTZ  DEFAULT now(),
  CONSTRAINT uq_appfollow_reviews UNIQUE (advertiser_name, review_id)
);

CREATE INDEX IF NOT EXISTS idx_appfollow_reviews_advertiser ON appfollow_reviews(advertiser_name);
CREATE INDEX IF NOT EXISTS idx_appfollow_reviews_date      ON appfollow_reviews(review_date);
CREATE INDEX IF NOT EXISTS idx_appfollow_reviews_sentiment ON appfollow_reviews(sentiment);
CREATE INDEX IF NOT EXISTS idx_appfollow_reviews_country   ON appfollow_reviews(country);
