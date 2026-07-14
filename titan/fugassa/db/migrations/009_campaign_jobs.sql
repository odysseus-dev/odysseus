-- Campaign job pipeline (HUD + FIFO GM / archivist / SD orchestration)

CREATE TABLE IF NOT EXISTS campaign_jobs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  save_id         TEXT NOT NULL,
  code            TEXT NOT NULL UNIQUE,
  job_type        TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'pending',
  priority        INTEGER NOT NULL DEFAULT 100,
  turn_number     INTEGER,
  batch_id        TEXT NOT NULL,
  depends_on_id   INTEGER REFERENCES campaign_jobs(id),
  payload_json    TEXT,
  result_json     TEXT,
  error           TEXT,
  attempts        INTEGER NOT NULL DEFAULT 0,
  max_attempts    INTEGER NOT NULL DEFAULT 3,
  created_at      TEXT NOT NULL,
  started_at      TEXT,
  finished_at     TEXT,
  updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_campaign_jobs_save_status
  ON campaign_jobs(save_id, status, priority, id);
CREATE INDEX IF NOT EXISTS idx_campaign_jobs_batch
  ON campaign_jobs(batch_id, status, priority, id);
