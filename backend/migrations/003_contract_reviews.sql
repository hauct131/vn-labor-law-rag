-- Contract review persistence. SQLAlchemy create_all applies equivalent DDL in local/Docker startup.
CREATE TABLE IF NOT EXISTS contract_reviews (
  id VARCHAR(36) PRIMARY KEY,
  user_id VARCHAR(36) NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  original_filename VARCHAR(255) NOT NULL,
  file_sha256 VARCHAR(64) NOT NULL,
  mime_type VARCHAR(120) NOT NULL,
  file_size_bytes INTEGER NOT NULL,
  retrieval_method VARCHAR(16) NOT NULL,
  status VARCHAR(32) NOT NULL,
  summary TEXT NOT NULL,
  extracted_character_count INTEGER NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_contract_reviews_user_id ON contract_reviews(user_id);
CREATE INDEX IF NOT EXISTS ix_contract_reviews_file_sha256 ON contract_reviews(file_sha256);

CREATE TABLE IF NOT EXISTS contract_review_findings (
  id VARCHAR(36) PRIMARY KEY,
  review_id VARCHAR(36) NOT NULL REFERENCES contract_reviews(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  category VARCHAR(80) NOT NULL,
  title VARCHAR(240) NOT NULL,
  severity VARCHAR(40) NOT NULL,
  contract_excerpt TEXT NOT NULL,
  analysis TEXT NOT NULL,
  recommendation TEXT NOT NULL,
  evidence_status VARCHAR(40) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_contract_review_findings_review_id ON contract_review_findings(review_id);

CREATE TABLE IF NOT EXISTS contract_review_sources (
  id VARCHAR(36) PRIMARY KEY,
  finding_id VARCHAR(36) NOT NULL REFERENCES contract_review_findings(id) ON DELETE CASCADE,
  source_id VARCHAR(50),
  chunk_id VARCHAR(200) NOT NULL,
  article_code VARCHAR(200),
  article_number VARCHAR(80),
  article_title VARCHAR(500),
  document_title VARCHAR(500),
  document_number VARCHAR(200),
  citation_label VARCHAR(500),
  clause_number VARCHAR(80),
  point_labels JSONB NOT NULL DEFAULT '[]'::jsonb,
  quoted_text TEXT NOT NULL,
  score DOUBLE PRECISION,
  source_rank INTEGER NOT NULL,
  retrieval_origin VARCHAR(100),
  source_type VARCHAR(50),
  source_url TEXT,
  component_ranks JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_contract_review_sources_finding_id ON contract_review_sources(finding_id);
