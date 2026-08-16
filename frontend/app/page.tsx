"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  Brain,
  Database,
  Loader2,
  MessageSquare,
  RefreshCw,
  Search,
  Sparkles,
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_MEMOE_API_BASE ?? "http://localhost:8000";

type Service = {
  slug: string;
  name: string;
  owner: string | null;
  criticality: string | null;
  event_count: number;
};

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

type MemoryHit = {
  memory_type: string;
  memory_id: string;
  hybrid_score: number;
  vector_similarity: number;
  service_slug: string | null;
  evidence_quality_rating: string | null;
};

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  retrievedMemory?: MemoryHit[];
  reflectionId?: string;
};

export default function Home() {
  const [services, setServices] = useState<Service[]>([]);
  const [observations, setObservations] = useState<Observation[]>([]);
  const [reflections, setReflections] = useState<Reflection[]>([]);
  const [selectedService, setSelectedService] = useState<string>("");
  const [message, setMessage] = useState(
    "What should SREs pay attention to for customer reports not covered by SLOs?",
  );
  const [reflect, setReflect] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chat, setChat] = useState<ChatMessage[]>([]);

  async function loadData() {
    setError(null);
    const [serviceRows, observationRows, reflectionRows] = await Promise.all([
      api<Service[]>("/services"),
      api<Observation[]>("/observations?limit=20"),
      api<Reflection[]>("/reflections?limit=20"),
    ]);
    setServices(serviceRows);
    setObservations(observationRows);
    setReflections(reflectionRows);
    if (!selectedService && serviceRows.length > 0) {
      setSelectedService(serviceRows[0].slug);
    }
  }

  useEffect(() => {
    loadData().catch((err: Error) => setError(err.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const scopedObservations = useMemo(() => {
    if (!selectedService) return observations;
    return observations.filter((row) => row.service_slug === selectedService);
  }, [observations, selectedService]);

  async function submitChat(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!message.trim()) return;
    setBusy("chat");
    setError(null);
    const userMessage = message.trim();
    setChat((rows) => [...rows, { role: "user", content: userMessage }]);

    try {
      const response = await api<{
        answer: string;
        retrieved_memory: MemoryHit[];
        reflection: { reflection_id: string } | null;
      }>("/chat", {
        method: "POST",
        body: JSON.stringify({
          message: userMessage,
          service_scope: selectedService || null,
          limit: 6,
          reflect,
        }),
      });
      setChat((rows) => [
        ...rows,
        {
          role: "assistant",
          content: response.answer,
          retrievedMemory: response.retrieved_memory,
          reflectionId: response.reflection?.reflection_id,
        },
      ]);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chat request failed");
    } finally {
      setBusy(null);
    }
  }

  async function runObservation() {
    if (!selectedService) return;
    setBusy("observation");
    setError(null);
    try {
      await api("/observations/run", {
        method: "POST",
        body: JSON.stringify({ service_slug: selectedService, provider: "bedrock" }),
      });
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Observation run failed");
    } finally {
      setBusy(null);
    }
  }

  async function runReflection() {
    setBusy("reflection");
    setError(null);
    try {
      await api("/reflections/run", {
        method: "POST",
        body: JSON.stringify({
          goal: message,
          service_scope: selectedService || null,
          provider: "bedrock",
          limit: 8,
        }),
      });
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reflection run failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <Database size={20} />
          <div>
            <h1>Memoe</h1>
            <p>Operational memory</p>
          </div>
        </div>

        <section className="panel">
          <div className="panel-header">
            <h2>Services</h2>
            <button className="icon-button" onClick={loadData} title="Refresh data">
              <RefreshCw size={16} />
            </button>
          </div>
          <div className="service-list">
            {services.map((service) => (
              <button
                key={service.slug}
                className={service.slug === selectedService ? "service active" : "service"}
                onClick={() => setSelectedService(service.slug)}
              >
                <span>{service.slug}</span>
                <small>{service.event_count} events</small>
              </button>
            ))}
          </div>
        </section>

        <section className="panel">
          <h2>Actions</h2>
          <button className="action-button" onClick={runObservation} disabled={busy !== null}>
            {busy === "observation" ? <Loader2 className="spin" size={16} /> : <Search size={16} />}
            Run observation
          </button>
          <button className="action-button" onClick={runReflection} disabled={busy !== null}>
            {busy === "reflection" ? <Loader2 className="spin" size={16} /> : <Brain size={16} />}
            Run reflection
          </button>
        </section>
      </aside>

      <section className="chat-panel">
        <div className="topbar">
          <div>
            <h2>Conversation</h2>
            <p>{selectedService ? `Scoped to ${selectedService}` : "All services"}</p>
          </div>
          <label className="toggle">
            <input
              type="checkbox"
              checked={reflect}
              onChange={(event) => setReflect(event.target.checked)}
            />
            Reflect before answer
          </label>
        </div>

        {error && <div className="error">{error}</div>}

        <div className="messages">
          {chat.length === 0 && (
            <div className="empty-state">
              <MessageSquare size={28} />
              <p>Ask about service risk, evidence gaps, or emerging operational patterns.</p>
            </div>
          )}
          {chat.map((item, index) => (
            <article key={`${item.role}-${index}`} className={`message ${item.role}`}>
              <div className="message-label">{item.role === "user" ? "You" : "Memoe"}</div>
              <p>{item.content}</p>
              {item.reflectionId && <small>Reflection stored: {item.reflectionId}</small>}
              {item.retrievedMemory && item.retrievedMemory.length > 0 && (
                <details>
                  <summary>Retrieved memory</summary>
                  <ul>
                    {item.retrievedMemory.map((memory) => (
                      <li key={`${memory.memory_type}-${memory.memory_id}`}>
                        {memory.memory_type} · {memory.memory_id.slice(0, 8)} · score{" "}
                        {memory.hybrid_score}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </article>
          ))}
        </div>

        <form className="composer" onSubmit={submitChat}>
          <textarea
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            rows={3}
          />
          <button disabled={busy !== null || !message.trim()}>
            {busy === "chat" ? <Loader2 className="spin" size={16} /> : <Sparkles size={16} />}
            Ask
          </button>
        </form>
      </section>

      <aside className="memory-panel">
        <section>
          <h2>Observations</h2>
          <div className="memory-list">
            {scopedObservations.map((observation) => (
              <MemoryCard
                key={observation.id}
                title={observation.observation_type}
                label={`${observation.service_slug} · ${observation.evidence_quality_rating}`}
                confidence={observation.confidence}
                statement={observation.statement}
              />
            ))}
          </div>
        </section>

        <section>
          <h2>Reflections</h2>
          <div className="memory-list">
            {reflections.map((reflection) => (
              <MemoryCard
                key={reflection.id}
                title={reflection.reflection_type}
                label={reflection.evidence_quality_rating}
                confidence={reflection.confidence}
                statement={reflection.statement}
              />
            ))}
          </div>
        </section>
      </aside>
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
