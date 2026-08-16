"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  Brain,
  Database,
  Loader2,
  MessageSquare,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
} from "lucide-react";
import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_MEMOE_API_BASE ?? "http://localhost:8000";

type Service = {
  slug: string;
  name: string;
  owner: string | null;
  criticality: string | null;
  event_count: number;
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
  request_payload: {
    goal?: string | null;
    service_scope?: string | null;
  };
  reflection_id: string | null;
  error_message: string | null;
  updated_at: string;
};

type ReflectionRunResponse = ReflectionJob;

type LatestReflection = {
  reflection_id: string;
  statement: string;
  confidence: number;
  evidence_quality: {
    rating?: string;
  };
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

type PersistedChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  retrieved_memory: MemoryHit[];
  reflection_id: string | null;
  created_at: string;
};

type ChatSession = {
  id: string;
  title: string;
  service_scope: string | null;
  status: string;
  working_memory: {
    turn_count?: number;
    current_goal?: string | null;
    active_services?: string[];
    last_reflection_id?: string | null;
  };
  messages: PersistedChatMessage[];
};

export default function Home() {
  const [services, setServices] = useState<Service[]>([]);
  const [reflections, setReflections] = useState<Reflection[]>([]);
  const [selectedService, setSelectedService] = useState<string>("");
  const [reflectionJob, setReflectionJob] = useState<ReflectionJob | null>(null);
  const [latestReflection, setLatestReflection] = useState<LatestReflection | null>(null);
  const [session, setSession] = useState<ChatSession | null>(null);
  const [message, setMessage] = useState("");
  const [reflect, setReflect] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chat, setChat] = useState<ChatMessage[]>([]);

  async function loadData() {
    setError(null);
    const [serviceRows, reflectionRows] = await Promise.all([
      api<Service[]>("/services"),
      api<Reflection[]>("/reflections?limit=20"),
    ]);
    setServices(serviceRows);
    setReflections(reflectionRows);
  }

  async function loadLatestSession() {
    const sessionRow = await api<ChatSession>("/chat/sessions/latest");
    setSession(sessionRow);
    setSelectedService(sessionRow.service_scope ?? "");
    setChat(sessionRow.messages.map(messageFromSession));
  }

  useEffect(() => {
    Promise.all([loadData(), loadLatestSession(), loadLatestReflectionJob()]).catch((err: Error) =>
      setError(err.message),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!reflectionJob || !isActiveReflectionJob(reflectionJob)) return;

    const timer = window.setInterval(() => {
      api<ReflectionJob>(`/reflections/jobs/${reflectionJob.id}`)
        .then((job) => {
          setReflectionJob(job);
          if (job.status === "completed") {
            loadData();
          }
        })
        .catch((err: Error) => setError(err.message));
    }, 1800);

    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reflectionJob?.id, reflectionJob?.status]);

  const newestReflection = useMemo(() => reflections[0] ?? null, [reflections]);

  async function submitChat(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!message.trim()) return;
    setBusy("chat");
    setError(null);
    const userMessage = message.trim();
    setChat((rows) => [...rows, { role: "user", content: userMessage }]);

    try {
      const response = await api<{
        session_id: string;
        answer: string;
        retrieved_memory: MemoryHit[];
        reflection: { reflection_id: string } | null;
        working_memory: ChatSession["working_memory"];
      }>("/chat", {
        method: "POST",
        body: JSON.stringify({
          session_id: session?.id ?? null,
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
      setSession((current) =>
        current
          ? {
              ...current,
              id: response.session_id,
              service_scope: selectedService || null,
              working_memory: response.working_memory,
            }
          : {
              id: response.session_id,
              title: userMessage,
              service_scope: selectedService || null,
              status: "active",
              working_memory: response.working_memory,
              messages: [],
            },
      );
      setMessage("");
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
      const result = await api<ReflectionRunResponse>("/reflections/run", {
        method: "POST",
        body: JSON.stringify({
          goal: message,
          service_scope: selectedService || null,
          provider: "bedrock",
          limit: 8,
        }),
      });
      setReflectionJob(result);
      setLatestReflection(null);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reflection run failed");
    } finally {
      setBusy(null);
    }
  }

  async function loadLatestReflectionJob() {
    const job = await api<ReflectionJob | null>("/reflections/jobs/latest");
    if (job && isActiveReflectionJob(job)) {
      setReflectionJob(job);
    }
  }

  async function startNewSession() {
    setBusy("session");
    setError(null);
    try {
      const sessionRow = await api<ChatSession>("/chat/sessions", {
        method: "POST",
        body: JSON.stringify({ service_scope: selectedService || null }),
      });
      setSession(sessionRow);
      setChat([]);
      setMessage("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Session creation failed");
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

        <section className="panel nav-panel">
          <Link href="/" className="nav-link active">
            Conversation
          </Link>
          <Link href="/memory" className="nav-link">
            Memory
          </Link>
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2>Services</h2>
            <button className="icon-button" onClick={loadData} title="Refresh data">
              <RefreshCw size={16} />
            </button>
          </div>
          <div className="service-list">
            <button
              className={selectedService === "" ? "service active" : "service"}
              onClick={() => setSelectedService("")}
            >
              <span>All services</span>
              <small>{services.reduce((total, service) => total + service.event_count, 0)} events</small>
            </button>
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
          <button className="action-button secondary" onClick={startNewSession} disabled={busy !== null}>
            {busy === "session" ? <Loader2 className="spin" size={16} /> : <Plus size={16} />}
            New session
          </button>
          <button
            className="action-button"
            onClick={runObservation}
            disabled={busy !== null || !selectedService}
          >
            {busy === "observation" ? <Loader2 className="spin" size={16} /> : <Search size={16} />}
            Run observation
          </button>
          {!selectedService && (
            <p className="action-note">Select one service before running an observation.</p>
          )}
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
            <p>
              {selectedService ? `Scoped to ${selectedService}` : "All services"} ·{" "}
              {session?.working_memory.turn_count ?? 0} turns saved
            </p>
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

        {reflectionJob && (
          <div className={`latest-memory ${reflectionJob.status === "failed" ? "error-memory" : ""}`}>
            <div>
              <strong>{reflectionJobTitle(reflectionJob)}</strong>
              <span>{new Date(reflectionJob.updated_at).toLocaleTimeString()}</span>
            </div>
            <p>{reflectionJobMessage(reflectionJob)}</p>
          </div>
        )}

        {!reflectionJob && latestReflection && (
          <div className="latest-memory">
            <div>
              <strong>New reflection stored</strong>
              <span>
                confidence {latestReflection.confidence.toFixed(2)} ·{" "}
                {latestReflection.evidence_quality.rating ?? "unknown evidence"}
              </span>
            </div>
            <p>{latestReflection.statement}</p>
          </div>
        )}

        {!reflectionJob && !latestReflection && newestReflection && (
          <div className="latest-memory muted-memory">
            <div>
              <strong>Latest reflection</strong>
              <span>
                {new Date(newestReflection.created_at).toLocaleString()} · confidence{" "}
                {newestReflection.confidence.toFixed(2)}
              </span>
            </div>
            <p>{newestReflection.statement}</p>
          </div>
        )}

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
            placeholder="Ask about service risk, evidence gaps, or emerging operational patterns."
            rows={3}
          />
          <button disabled={busy !== null || !message.trim()}>
            {busy === "chat" ? <Loader2 className="spin" size={16} /> : <Sparkles size={16} />}
            Ask
          </button>
        </form>
      </section>

    </main>
  );
}

function messageFromSession(row: PersistedChatMessage): ChatMessage {
  return {
    role: row.role,
    content: row.content,
    retrievedMemory: row.retrieved_memory,
    reflectionId: row.reflection_id ?? undefined,
  };
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
