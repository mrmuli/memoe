# Provider Comparison: Ollama and Amazon Bedrock

Memoe now supports two model providers for operational observations:

- Ollama Cloud with `gpt-oss:20b`
- Amazon Bedrock with `openai.gpt-oss-20b-1:0`

Both providers use the same stored procedure, evidence bundle, JSON output contract, and CockroachDB persistence path. This keeps provider behavior comparable.

## What We Learned

The first Payments observation worked with Ollama and produced a moderate-confidence deployment-impact hypothesis. After telemetry logs were added, the observation included stronger evidence gaps: missing request-level trace correlation, missing diagnostic logs, missing historical baseline, missing config details, and missing rollback or recovery evidence.

The first Notifications observation exposed a chronology failure. The model treated a deployment at `10:20` as possibly related to degradation that had already started at `10:17:45` and `10:18:00`.

We added a chronology rule to procedural memory:

```text
Do not suggest deployment impact if degradation signals, error telemetry, or customer outcomes began before the deployment, unless there is explicit evidence that the deployment or rollout had already started earlier.
```

That changed Notifications from a deployment-impact observation to an inconclusive observation.

Bedrock then exposed a second issue: it respected chronology, but overclaimed the negative conclusion by saying the deployment was not the cause with high confidence. We added a negative-conclusion rule:

```text
Do not claim that a deployment, service, dependency, or change was not the cause unless the evidence includes direct exclusionary support.
```

That lowered the Notifications confidence and made the statement more cautious.

## Why This Matters

This is the core Memoe argument:

```text
Evidence is stored in CockroachDB.
Procedural memory is stored in CockroachDB.
Model runs are stored in CockroachDB.
Observations are stored in CockroachDB.
Reflection turns observations into higher-level memory.
```

The provider can change, but the memory layer remains stable.

## Current Provider Behavior

Ollama was useful for fast iteration and exposed whether the procedure was clear enough.

Bedrock gave us the AWS path required for the hackathon and revealed model-runtime behavior we had to handle: `openai.gpt-oss-20b-1:0` needed a larger output token budget to produce final JSON after reasoning.

The current Bedrock max output token default is `4096`.

## Remaining Questions

- Should confidence be calibrated separately from evidence quality?
- Should reflections prefer observations from one provider or compare providers explicitly?
- Should Memoe store provider disagreements as a first-class signal?
- What evidence is enough to rule out a cause?
