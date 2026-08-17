# Memoe Agent Instructions

These instructions apply to work inside the `memoe` project.

## Collaboration Style

The user wants to build Memoe together step by step.

Prefer:

- straightforward, digestible responses;
- one implementation step at a time;
- clear tradeoffs before decisions;
- explicit permission before moving to the next major step;
- short summaries of what changed and why;
- explanations that support learning without long lectures.

Do not race ahead through multiple architecture or implementation stages without checking in.

Do not default to conservative or "safe" experimental choices unless there is a real danger, security risk, compliance risk, destructive operation, cost concern, or explicit user constraint.

For product and architecture experiments, aim at the meaningful unknowns directly. Do not dilute the experiment with placeholder paths unless the user asks for them or they are needed to isolate a specific failure mode.

Do not assume the user wants a cheaper, safer, simpler, or less ambitious path. State the tradeoff and ask when the direction is unclear.

Do not prefer hardcoded product-understanding or routing logic for Memoe. For example, do not silently add keyword classifiers for concepts such as SLOs, logs, tickets, deployments, traces, incidents, or service risks. Prefer model-routed or data-driven behavior, with deterministic code reserved for validation, persistence, security, cost control, and guardrails. If a hardcoded shortcut is being considered for time pressure, call it out explicitly and ask first.

## Permission Before Progressing

Before starting each next major step, ask for permission.

Examples of major steps:

- scaffolding the repository;
- choosing or installing dependencies;
- creating the database schema;
- adding seed data;
- implementing the investigation engine;
- adding Bedrock integration;
- adding frontend screens;
- preparing deployment;
- using cloud services.

Small verification commands and file reads do not require separate permission.

## Preferred Technical Direction

Prefer Python for data-heavy and backend logic.

Python should be the default choice for:

- ingestion;
- synthetic data loading;
- correlation logic;
- investigation orchestration;
- provider interfaces;
- Bedrock integration;
- CockroachDB access;
- tests around data behavior.

Inference providers should be swappable behind provider interfaces.

The planned direction is:

- `BedrockObservationProvider` for the hackathon AWS path;
- `RecordedObservationProvider` for replaying real model responses during repeatable local tests;
- an additional non-AWS observation provider may be used while AWS sandbox access is pending.

Avoid making `FakeObservationProvider` the main experiment path unless explicitly requested. The core experiment should test real model behavior.

Next.js and TypeScript are welcome when they clearly help the product, especially for:

- Memoe Lens frontend;
- dashboard views;
- service overview;
- event timeline;
- observation and investigation details;
- reflection controls.

Do not assume the whole project should be Next.js or TypeScript.

## Database Guidance

Use local CockroachDB for development and testing where possible.

Always inform the user before work requires:

- CockroachDB Cloud;
- AWS credentials;
- Amazon Bedrock access;
- Docker services;
- network dependency installation;
- external API credentials;
- production deployment.

CockroachDB should remain the system of record for Memoe's operational memory.

## Current Product Direction

Treat `ideas/v0-1.md` as the current source of truth while Memoe is still in planning and prototyping.

Memoe is an operational memory system for software services.

Its main question is:

> What should I pay attention to today, and why?

Technical north star:

> Events are evidence. Investigations connect evidence. Observations interpret evidence. Reflection turns accumulated observations into operational knowledge.

## Initial Build Bias

Build one vertical slice before expanding architecture.

Prefer the early order:

1. Repository skeleton.
2. CockroachDB schema.
3. Synthetic data loader.
4. Service, EventSource, and Event persistence.
5. Minimal read-only Memoe Lens for events.
6. Investigation persistence.
7. Deterministic investigation trigger.
8. Rule-based correlation.
9. Bedrock ObservationProvider.

Do not block the first useful demo on vector retrieval, real integrations, or production infrastructure.

## Explicitly Deferred Unless Requested

Do not implement these early unless the user explicitly agrees:

- real GitHub integration;
- real Jira integration;
- real CloudWatch integration;
- Steampipe or Powerpipe;
- EventBridge, SQS, or Lambda;
- Kubernetes;
- Hermes integration;
- autonomous remediation;
- complex temporal reasoning;
- multiple correlation algorithms;
- advanced knowledge lifecycle scoring.
