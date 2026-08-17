"use client";

import { useEffect, useState } from "react";
import { Database, RefreshCw } from "lucide-react";
import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_MEMOE_API_BASE ?? "http://localhost:8000";

type MemoryView = "reflections" | "observations";

type Observation = {
  id: string;
  service_slug: string;
  created_at: string;
  model_id: string;
  observation_type: string;
  confidence: number;
  evidence_quality_rating: string;
  lifecycle_status: string;
  statement: string;
  occurrence_count?: number;
  first_seen_at?: string;
  last_seen_at?: string;
};

type Reflection = {
  id: string;
  created_at: string;
  model_id: string;
  reflection_type: string;
  confidence: number;
  evidence_quality_rating: string;
  statement: string;
  occurrence_count?: number;
  first_seen_at?: string;
  last_seen_at?: string;
};

type ReflectionJob = {
  id: string;
  status: "queued" | "running" | "embedding" | "completed" | "failed";
  stage: string;
  reflection_id: string | null;
  error_message: string | null;
  updated_at: string;
};

export default function MemoryPage() {
  const [observations, setObservations] = useState<Observation[]>([]);
  const [reflections, setReflections] = useState<Reflection[]>([]);
  const [memoryView, setMemoryView] = useState<MemoryView>("reflections");
  const [reflectionJob, setReflectionJob] = useState<ReflectionJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadMemory() {
    setBusy(true);
    setError(null);
    try {
      const [observationRows, reflectionRows] = await Promise.all([
        api<Observation[]>("/observations?limit=50"),
        api<Reflection[]>("/reflections?limit=50"),
      ]);
      setObservations(observationRows);
      setReflections(reflectionRows);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Memory load failed");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    loadMemory();
    loadLatestReflectionJob();
  }, []);

  useEffect(() => {
    if (!reflectionJob || !isActiveReflectionJob(reflectionJob)) return;

    const timer = window.setInterval(() => {
      api<ReflectionJob>(`/reflections/jobs/${reflectionJob.id}`)
        .then((job) => {
          setReflectionJob(job);
          if (job.status === "completed") {
            loadMemory();
          }
        })
        .catch((err: Error) => setError(err.message));
    }, 1800);

    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reflectionJob?.id, reflectionJob?.status]);

  async function loadLatestReflectionJob() {
    const job = await api<ReflectionJob | null>("/reflections/jobs/latest");
    if (job && isActiveReflectionJob(job)) {
      setReflectionJob(job);
    }
  }

  return (
    <main className="memory-shell">
      <aside className="sidebar">
        <div className="brand">
          <Database size={20} />
          <div>
            <h1>Memoe</h1>
            <p>Operational memory</p>
          </div>
        </div>

        <section className="panel nav-panel">
          <Link href="/" className="nav-link">
            Conversation
          </Link>
          <Link href="/memory" className="nav-link active">
            Memory
          </Link>
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2>Memory</h2>
            <button className="icon-button" onClick={loadMemory} disabled={busy} title="Refresh memory">
              <RefreshCw className={busy ? "spin" : undefined} size={16} />
            </button>
          </div>
          <p className="action-note">
            Newest observations and reflections appear first.
          </p>
        </section>
      </aside>

      <section className="memory-workspace">
        <div className="memory-header">
          <div>
            <h2>Observations and Reflections</h2>
            <p>Evidence-backed memory stored in CockroachDB.</p>
          </div>
        </div>

        {error && <div className="error">{error}</div>}

        {reflectionJob && (
          <div className={`latest-memory ${reflectionJob.status === "failed" ? "error-memory" : ""}`}>
            <div>
              <strong>{reflectionJobTitle(reflectionJob)}</strong>
              <span>{new Date(reflectionJob.updated_at).toLocaleTimeString()}</span>
            </div>
            <p>{reflectionJobMessage(reflectionJob)}</p>
          </div>
        )}

        <div className="memory-filter" aria-label="Memory type">
          <button
            className={memoryView === "reflections" ? "active" : undefined}
            onClick={() => setMemoryView("reflections")}
            type="button"
          >
            Reflections <span>{reflections.length}</span>
          </button>
          <button
            className={memoryView === "observations" ? "active" : undefined}
            onClick={() => setMemoryView("observations")}
            type="button"
          >
            Observations <span>{observations.length}</span>
          </button>
        </div>

        <section className="memory-section memory-section-full">
          <div className="section-heading">
            <h2>{memoryView === "reflections" ? "Reflections" : "Observations"}</h2>
            <span>{memoryView === "reflections" ? reflections.length : observations.length}</span>
          </div>
          <div className="memory-list">
            {memoryView === "reflections"
              ? reflections.map((reflection) => (
                  <MemoryCard
                    key={reflection.id}
                    title={reflection.reflection_type}
                    label={`${reflection.evidence_quality_rating} · seen ${
                      reflection.occurrence_count ?? 1
                    }x · ${formatDate(reflection.last_seen_at ?? reflection.created_at)}`}
                    confidence={reflection.confidence}
                    statement={reflection.statement}
                  />
                ))
              : observations.map((observation) => (
                  <MemoryCard
                    key={observation.id}
                    title={observation.observation_type}
                    label={`${observation.service_slug} · ${
                      observation.evidence_quality_rating
                    } · seen ${observation.occurrence_count ?? 1}x · ${formatDate(
                      observation.last_seen_at ?? observation.created_at,
                    )}`}
                    confidence={observation.confidence}
                    statement={observation.statement}
                  />
                ))}
          </div>
        </section>
      </section>
    </main>
  );
}

function MemoryCard({
  title,
  label,
  confidence,
  statement,
}: {
  title: string;
  label: string;
  confidence: number;
  statement: string;
}) {
  return (
    <article className="memory-card">
      <div>
        <strong>{title}</strong>
        <span>{label}</span>
      </div>
      <p>{statement}</p>
      <small>confidence {confidence.toFixed(2)}</small>
    </article>
  );
}

function formatDate(value: string) {
  return new Date(value).toLocaleString();
}

function isActiveReflectionJob(job: ReflectionJob) {
  return job.status === "queued" || job.status === "running" || job.status === "embedding";
}

function reflectionJobTitle(job: ReflectionJob) {
  if (job.status === "queued") return "Reflection queued";
  if (job.status === "running") return "Memoe is reflecting";
  if (job.status === "embedding") return "Updating memory search";
  if (job.status === "completed") return "New reflection stored";
  return "Reflection failed";
}

function reflectionJobMessage(job: ReflectionJob) {
  if (job.status === "queued") return "Memoe is waiting to start the reflection.";
  if (job.status === "running") return "Memoe is reviewing observations, prior reflections, and retrieved memory.";
  if (job.status === "embedding") return "The reflection is stored; Memoe is refreshing embeddings so chat can use it.";
  if (job.status === "completed") return "Reflection is complete and available to chat.";
  return job.error_message ?? "Reflection failed.";
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? `Request failed: ${response.status}`);
  }
  return response.json();
}
