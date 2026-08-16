"use client";

import { useEffect, useState } from "react";
import { Database, RefreshCw } from "lucide-react";
import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_MEMOE_API_BASE ?? "http://localhost:8000";

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
};

type Reflection = {
  id: string;
  created_at: string;
  model_id: string;
  reflection_type: string;
  confidence: number;
  evidence_quality_rating: string;
  statement: string;
};

export default function MemoryPage() {
  const [observations, setObservations] = useState<Observation[]>([]);
  const [reflections, setReflections] = useState<Reflection[]>([]);
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
  }, []);

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

        <div className="memory-grid">
          <section className="memory-section">
            <div className="section-heading">
              <h2>Reflections</h2>
              <span>{reflections.length}</span>
            </div>
            <div className="memory-list">
              {reflections.map((reflection) => (
                <MemoryCard
                  key={reflection.id}
                  title={reflection.reflection_type}
                  label={`${reflection.evidence_quality_rating} · ${formatDate(reflection.created_at)}`}
                  confidence={reflection.confidence}
                  statement={reflection.statement}
                />
              ))}
            </div>
          </section>

          <section className="memory-section">
            <div className="section-heading">
              <h2>Observations</h2>
              <span>{observations.length}</span>
            </div>
            <div className="memory-list">
              {observations.map((observation) => (
                <MemoryCard
                  key={observation.id}
                  title={observation.observation_type}
                  label={`${observation.service_slug} · ${observation.evidence_quality_rating} · ${formatDate(
                    observation.created_at,
                  )}`}
                  confidence={observation.confidence}
                  statement={observation.statement}
                />
              ))}
            </div>
          </section>
        </div>
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
