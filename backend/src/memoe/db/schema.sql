CREATE TABLE IF NOT EXISTS services (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug STRING NOT NULL UNIQUE,
  name STRING NOT NULL,
  description STRING NULL,
  owner STRING NULL,
  criticality STRING NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS event_sources (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service_id UUID NULL REFERENCES services(id),
  source_type STRING NOT NULL,
  provider STRING NOT NULL,
  name STRING NOT NULL,
  external_reference STRING NOT NULL,
  component STRING NULL,
  environment STRING NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (provider, source_type, external_reference)
);

CREATE TABLE IF NOT EXISTS events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service_id UUID NULL REFERENCES services(id),
  event_source_id UUID NULL REFERENCES event_sources(id),
  source_table STRING NOT NULL,
  source_id STRING NOT NULL,
  source_type STRING NOT NULL,
  category STRING NOT NULL,
  event_type STRING NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  component STRING NULL,
  severity STRING NULL,
  summary STRING NOT NULL,
  external_reference STRING NULL,
  correlation_identifiers JSONB NOT NULL DEFAULT '{}'::JSONB,
  metadata JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source_table, source_id),
  CONSTRAINT events_category_check CHECK (category IN ('signal', 'event', 'outcome', 'telemetry'))
);

CREATE INDEX IF NOT EXISTS events_service_occurred_at_idx
  ON events (service_id, occurred_at);

CREATE INDEX IF NOT EXISTS events_category_event_type_idx
  ON events (category, event_type);

ALTER TABLE event_sources ADD COLUMN IF NOT EXISTS component STRING NULL;

ALTER TABLE events ADD COLUMN IF NOT EXISTS component STRING NULL;

ALTER TABLE events DROP CONSTRAINT IF EXISTS events_category_check;

ALTER TABLE events ADD CONSTRAINT events_category_check
  CHECK (category IN ('signal', 'event', 'outcome', 'telemetry'));

CREATE TABLE IF NOT EXISTS procedures (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name STRING NOT NULL,
  version INT NOT NULL,
  status STRING NOT NULL,
  purpose STRING NOT NULL,
  instructions STRING NOT NULL,
  output_schema JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (name, version),
  CONSTRAINT procedures_status_check CHECK (status IN ('active', 'inactive'))
);

CREATE TABLE IF NOT EXISTS observation_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service_id UUID NULL REFERENCES services(id),
  procedure_id UUID NOT NULL REFERENCES procedures(id),
  procedure_name STRING NOT NULL,
  procedure_version INT NOT NULL,
  provider STRING NOT NULL,
  model_id STRING NOT NULL,
  status STRING NOT NULL,
  time_window_start TIMESTAMPTZ NULL,
  time_window_end TIMESTAMPTZ NULL,
  request_payload JSONB NOT NULL,
  raw_response JSONB NULL,
  error_message STRING NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ NULL,
  CONSTRAINT observation_runs_status_check
    CHECK (status IN ('pending', 'running', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS observation_runs_service_started_at_idx
  ON observation_runs (service_id, started_at);

CREATE TABLE IF NOT EXISTS observations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service_id UUID NULL REFERENCES services(id),
  observation_run_id UUID NOT NULL REFERENCES observation_runs(id),
  procedure_id UUID NOT NULL REFERENCES procedures(id),
  statement STRING NOT NULL,
  observation_type STRING NOT NULL,
  confidence DECIMAL NOT NULL,
  evidence_quality JSONB NOT NULL DEFAULT '{}'::JSONB,
  details JSONB NOT NULL DEFAULT '{}'::JSONB,
  limitations JSONB NOT NULL DEFAULT '[]'::JSONB,
  reasoning_summary STRING NOT NULL,
  lifecycle_status STRING NOT NULL DEFAULT 'fresh',
  valid_from TIMESTAMPTZ NOT NULL DEFAULT now(),
  valid_until TIMESTAMPTZ NULL,
  stale_after TIMESTAMPTZ NULL,
  last_checked_at TIMESTAMPTZ NULL,
  superseded_by_observation_id UUID NULL REFERENCES observations(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT observations_confidence_check CHECK (confidence >= 0 AND confidence <= 1),
  CONSTRAINT observations_lifecycle_status_check CHECK (
    lifecycle_status IN (
      'fresh',
      'stale',
      'superseded',
      'validated',
      'weakened',
      'needs_recheck'
    )
  ),
  CONSTRAINT observations_type_check CHECK (
    observation_type IN (
      'deployment_impact',
      'recurring_pattern',
      'recovery_pattern',
      'hotspot',
      'emerging_trend',
      'memoe_system',
      'inconclusive'
    )
  )
);

CREATE INDEX IF NOT EXISTS observations_service_created_at_idx
  ON observations (service_id, created_at);

ALTER TABLE observations ADD COLUMN IF NOT EXISTS evidence_quality JSONB NOT NULL DEFAULT '{}'::JSONB;

ALTER TABLE observations ADD COLUMN IF NOT EXISTS details JSONB NOT NULL DEFAULT '{}'::JSONB;

ALTER TABLE observations ADD COLUMN IF NOT EXISTS lifecycle_status STRING NOT NULL DEFAULT 'fresh';

ALTER TABLE observations ADD COLUMN IF NOT EXISTS valid_from TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE observations ADD COLUMN IF NOT EXISTS valid_until TIMESTAMPTZ NULL;

ALTER TABLE observations ADD COLUMN IF NOT EXISTS stale_after TIMESTAMPTZ NULL;

ALTER TABLE observations ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ NULL;

ALTER TABLE observations ADD COLUMN IF NOT EXISTS superseded_by_observation_id UUID NULL REFERENCES observations(id);

ALTER TABLE observations DROP CONSTRAINT IF EXISTS observations_lifecycle_status_check;

ALTER TABLE observations ADD CONSTRAINT observations_lifecycle_status_check CHECK (
  lifecycle_status IN (
    'fresh',
    'stale',
    'superseded',
    'validated',
    'weakened',
    'needs_recheck'
  )
);

CREATE TABLE IF NOT EXISTS observation_evidence (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  observation_run_id UUID NOT NULL REFERENCES observation_runs(id),
  observation_id UUID NULL REFERENCES observations(id),
  event_id UUID NOT NULL REFERENCES events(id),
  role STRING NOT NULL,
  reason STRING NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (observation_run_id, event_id, role),
  CONSTRAINT observation_evidence_role_check CHECK (role IN ('considered', 'supporting', 'rejected'))
);

CREATE TABLE IF NOT EXISTS reflection_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  procedure_id UUID NOT NULL REFERENCES procedures(id),
  procedure_name STRING NOT NULL,
  procedure_version INT NOT NULL,
  provider STRING NOT NULL,
  model_id STRING NOT NULL,
  status STRING NOT NULL,
  request_payload JSONB NOT NULL,
  raw_response JSONB NULL,
  error_message STRING NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ NULL,
  CONSTRAINT reflection_runs_status_check
    CHECK (status IN ('pending', 'running', 'completed', 'failed'))
);

CREATE TABLE IF NOT EXISTS reflections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  reflection_run_id UUID NOT NULL REFERENCES reflection_runs(id),
  procedure_id UUID NOT NULL REFERENCES procedures(id),
  statement STRING NOT NULL,
  reflection_type STRING NOT NULL,
  confidence DECIMAL NOT NULL,
  evidence_quality JSONB NOT NULL DEFAULT '{}'::JSONB,
  limitations JSONB NOT NULL DEFAULT '[]'::JSONB,
  reasoning_summary STRING NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT reflections_confidence_check CHECK (confidence >= 0 AND confidence <= 1)
);

ALTER TABLE reflections ADD COLUMN IF NOT EXISTS details JSONB NOT NULL DEFAULT '{}'::JSONB;

ALTER TABLE reflections DROP CONSTRAINT IF EXISTS reflections_type_check;

CREATE INDEX IF NOT EXISTS reflections_created_at_idx
  ON reflections (created_at);

CREATE TABLE IF NOT EXISTS reflection_observations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  reflection_run_id UUID NOT NULL REFERENCES reflection_runs(id),
  reflection_id UUID NULL REFERENCES reflections(id),
  observation_id UUID NOT NULL REFERENCES observations(id),
  role STRING NOT NULL,
  reason STRING NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (reflection_run_id, observation_id, role),
  CONSTRAINT reflection_observations_role_check
    CHECK (role IN ('considered', 'supporting', 'rejected'))
);

CREATE TABLE IF NOT EXISTS validation_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source STRING NOT NULL,
  status STRING NOT NULL,
  query JSONB NOT NULL DEFAULT '{}'::JSONB,
  raw_response JSONB NULL,
  error_message STRING NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ NULL,
  CONSTRAINT validation_runs_status_check CHECK (status IN ('pending', 'running', 'completed', 'failed'))
);

CREATE TABLE IF NOT EXISTS validation_results (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  validation_run_id UUID NOT NULL REFERENCES validation_runs(id),
  observation_id UUID NULL REFERENCES observations(id),
  reflection_id UUID NULL REFERENCES reflections(id),
  source STRING NOT NULL,
  result_type STRING NOT NULL,
  summary STRING NOT NULL,
  evidence JSONB NOT NULL DEFAULT '{}'::JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT validation_results_target_check CHECK (
    observation_id IS NOT NULL OR reflection_id IS NOT NULL
  ),
  CONSTRAINT validation_results_type_check CHECK (
    result_type IN ('validated', 'weakened', 'superseded', 'needs_recheck', 'inconclusive')
  )
);

CREATE INDEX IF NOT EXISTS validation_results_observation_created_at_idx
  ON validation_results (observation_id, created_at);

CREATE INDEX IF NOT EXISTS validation_results_reflection_created_at_idx
  ON validation_results (reflection_id, created_at);

CREATE TABLE IF NOT EXISTS memory_embeddings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  memory_type STRING NOT NULL,
  memory_id UUID NOT NULL,
  embedding_model STRING NOT NULL,
  embedding VECTOR(256) NULL,
  embedding_384 VECTOR(384) NULL,
  embedded_text STRING NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (memory_type, memory_id, embedding_model),
  CONSTRAINT memory_embeddings_one_vector_check CHECK (
    (embedding IS NOT NULL AND embedding_384 IS NULL)
    OR (embedding IS NULL AND embedding_384 IS NOT NULL)
  ),
  CONSTRAINT memory_embeddings_type_check CHECK (
    memory_type IN ('observation', 'reflection', 'validation_result')
  )
);

CREATE INDEX IF NOT EXISTS memory_embeddings_type_model_idx
  ON memory_embeddings (memory_type, embedding_model);

CREATE TABLE IF NOT EXISTS chat_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title STRING NOT NULL,
  service_scope STRING NULL,
  status STRING NOT NULL DEFAULT 'active',
  working_memory JSONB NOT NULL DEFAULT '{}'::JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT chat_sessions_status_check CHECK (status IN ('active', 'archived'))
);

CREATE INDEX IF NOT EXISTS chat_sessions_status_updated_at_idx
  ON chat_sessions (status, updated_at);

CREATE TABLE IF NOT EXISTS chat_messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role STRING NOT NULL,
  content STRING NOT NULL,
  retrieved_memory JSONB NOT NULL DEFAULT '[]'::JSONB,
  reflection_id UUID NULL REFERENCES reflections(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT chat_messages_role_check CHECK (role IN ('user', 'assistant'))
);

CREATE INDEX IF NOT EXISTS chat_messages_session_created_at_idx
  ON chat_messages (session_id, created_at);
