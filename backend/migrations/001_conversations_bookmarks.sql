BEGIN;

CREATE TABLE IF NOT EXISTS app_users (
    id VARCHAR(36) PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conversations (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    title VARCHAR(160) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_conversations_user_id ON conversations(user_id);

CREATE TABLE IF NOT EXISTS messages (
    id VARCHAR(36) PRIMARY KEY,
    conversation_id VARCHAR(36) NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role VARCHAR(16) NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    corpus_release_id VARCHAR(200),
    retrieval_method VARCHAR(16),
    retrieval_ms DOUBLE PRECISION,
    generation_ms DOUBLE PRECISION,
    total_ms DOUBLE PRECISION,
    model VARCHAR(300),
    insufficient_evidence BOOLEAN NOT NULL DEFAULT FALSE,
    out_of_scope BOOLEAN NOT NULL DEFAULT FALSE,
    generation_failed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_messages_conversation_id ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS ix_messages_created_at ON messages(created_at);

CREATE TABLE IF NOT EXISTS message_sources (
    id VARCHAR(36) PRIMARY KEY,
    message_id VARCHAR(36) NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
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
CREATE INDEX IF NOT EXISTS ix_message_sources_message_id ON message_sources(message_id);
CREATE INDEX IF NOT EXISTS ix_message_sources_article_code ON message_sources(article_code);

CREATE TABLE IF NOT EXISTS bookmarks (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    message_id VARCHAR(36) NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_bookmark_user_message UNIQUE (user_id, message_id)
);
CREATE INDEX IF NOT EXISTS ix_bookmarks_user_id ON bookmarks(user_id);
CREATE INDEX IF NOT EXISTS ix_bookmarks_message_id ON bookmarks(message_id);

COMMIT;
