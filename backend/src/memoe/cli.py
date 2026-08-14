"""CLI entrypoint for Memoe."""

from typing import Annotated

import typer

from memoe.config import Settings

app = typer.Typer(help="Memoe operational memory CLI.")
database_app = typer.Typer(help="Manage the local Memoe database.")
seed_app = typer.Typer(help="Load synthetic evidence fixtures.")
evidence_app = typer.Typer(help="Inspect normalized operational evidence.")
observations_app = typer.Typer(help="Run and inspect observation generation.")
reflections_app = typer.Typer(help="Run and inspect reflection generation.")

app.add_typer(database_app, name="database")
app.add_typer(seed_app, name="seed")
app.add_typer(evidence_app, name="evidence")
app.add_typer(observations_app, name="observations")
app.add_typer(reflections_app, name="reflections")


@app.command()
def config() -> None:
    """Show non-secret runtime configuration."""
    settings = Settings()
    typer.echo(f"DATABASE_URL={settings.database_url}")
    typer.echo(f"OBSERVATION_PROVIDER={settings.observation_provider}")
    typer.echo(f"OLLAMA_BASE_URL={settings.ollama_base_url or ''}")
    typer.echo(f"OLLAMA_MODEL={settings.ollama_model or ''}")
    typer.echo(f"OLLAMA_API_KEY_SET={bool(settings.ollama_api_key)}")
    typer.echo(f"AWS_REGION={settings.aws_region or ''}")
    typer.echo(f"AWS_PROFILE={settings.aws_profile or ''}")
    typer.echo(f"BEDROCK_MODEL_ID={settings.bedrock_model_id or ''}")


@database_app.command("init")
def database_init() -> None:
    """Create the Memoe database schema."""
    from memoe.db.init import initialize_database

    settings = Settings()
    initialize_database(settings)
    typer.echo("Database schema initialized.")


@seed_app.command("load")
def seed_load(
    scenario: Annotated[str, typer.Argument(help="Fixture scenario name, e.g. payments.")],
) -> None:
    """Load a synthetic fixture scenario."""
    from memoe.services.seed_loader import load_seed_scenario

    result = load_seed_scenario(scenario, Settings())
    typer.echo(f"Loaded seed scenario: {result.scenario}")
    typer.echo(f"Services: {result.services}")
    typer.echo(f"Event sources: {result.event_sources}")
    typer.echo(f"Events: {result.events}")
    typer.echo(f"Procedures: {result.procedures}")


@evidence_app.command("list")
def evidence_list(
    service: Annotated[str, typer.Option(help="Service slug, e.g. payments.")],
) -> None:
    """List normalized evidence for a service."""
    from memoe.services.seed_loader import list_evidence

    rows = list_evidence(service, Settings())
    if not rows:
        typer.echo(f"No evidence found for service: {service}")
        return

    for row in rows:
        typer.echo(
            " | ".join(
                [
                    row.occurred_at,
                    row.service_slug,
                    row.category,
                    row.event_type,
                    row.component or "-",
                    row.source_table,
                    row.summary,
                ]
            )
        )


@observations_app.command("run")
def observations_run(
    service: Annotated[str, typer.Option(help="Service slug, e.g. payments.")],
    provider: Annotated[
        str | None,
        typer.Option(help="Observation provider override, e.g. ollama or bedrock."),
    ] = None,
) -> None:
    """Run observation generation for a service."""
    from memoe.services.observation_runner import run_observation

    settings = Settings()
    selected_provider = provider or settings.observation_provider
    try:
        result = run_observation(service, selected_provider, settings)
    except Exception as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"Observation run: {result.run_id}")
    typer.echo(f"Observation: {result.observation_id}")
    typer.echo(f"Confidence: {result.confidence}")
    typer.echo(f"Statement: {result.statement}")
    if result.evidence_quality:
        typer.echo(f"Evidence quality: {result.evidence_quality}")
    typer.echo(f"Supporting evidence IDs: {', '.join(result.supporting_evidence_ids) or '-'}")
    typer.echo(f"Rejected evidence IDs: {', '.join(result.rejected_evidence_ids) or '-'}")
    if result.limitations:
        typer.echo("Limitations:")
        for limitation in result.limitations:
            typer.echo(f"- {limitation}")


@observations_app.command("show")
def observations_show(
    target: Annotated[str, typer.Argument(help="Observation selector, e.g. latest.")],
) -> None:
    """Show a stored observation."""
    from memoe.services.observation_runner import latest_observation

    if target != "latest":
        raise typer.BadParameter("Only 'latest' is supported for now.")

    observation = latest_observation(Settings())
    if not observation:
        typer.echo("No observations found.")
        return

    typer.echo(f"Observation: {observation.id}")
    typer.echo(f"Service: {observation.service_slug}")
    typer.echo(f"Type: {observation.observation_type}")
    typer.echo(f"Confidence: {observation.confidence}")
    typer.echo(f"Model: {observation.model_id}")
    typer.echo(f"Procedure: {observation.procedure_name} v{observation.procedure_version}")
    typer.echo(f"Statement: {observation.statement}")
    typer.echo(f"Evidence quality: {observation.evidence_quality}")
    typer.echo(f"Reasoning: {observation.reasoning_summary}")

    if observation.limitations:
        typer.echo("Limitations:")
        for limitation in observation.limitations:
            typer.echo(f"- {limitation}")

    typer.echo("Evidence:")
    for evidence in observation.evidence:
        typer.echo(
            " | ".join(
                [
                    str(evidence["role"]),
                    str(evidence["occurred_at"]),
                    str(evidence["category"]),
                    str(evidence["event_type"]),
                    str(evidence["component"] or "-"),
                    str(evidence["summary"]),
                ]
            )
        )


@observations_app.command("list")
def observations_list(
    service: Annotated[
        str | None,
        typer.Option(help="Optional service slug filter, e.g. payments."),
    ] = None,
    limit: Annotated[int, typer.Option(help="Maximum number of observations to show.")] = 10,
) -> None:
    """List recent stored observations."""
    from memoe.services.observation_runner import list_observations

    rows = list_observations(service_slug=service, limit=limit, settings=Settings())
    if not rows:
        typer.echo("No observations found.")
        return

    for row in rows:
        typer.echo(
            " | ".join(
                [
                    row.created_at,
                    row.service_slug,
                    row.model_id,
                    row.observation_type,
                    f"confidence={row.confidence}",
                    f"quality={row.evidence_quality_rating}",
                    row.statement,
                ]
            )
        )


@reflections_app.command("run")
def reflections_run(
    provider: Annotated[
        str | None,
        typer.Option(help="Reflection provider override, e.g. ollama or bedrock."),
    ] = None,
    limit: Annotated[int, typer.Option(help="Maximum recent observations to reflect over.")] = 10,
) -> None:
    """Run reflection generation over stored observations."""
    from memoe.services.reflection_runner import run_reflection

    settings = Settings()
    selected_provider = provider or settings.observation_provider
    try:
        result = run_reflection(selected_provider, limit=limit, settings=settings)
    except Exception as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"Reflection run: {result.run_id}")
    typer.echo(f"Reflection: {result.reflection_id}")
    typer.echo(f"Confidence: {result.confidence}")
    typer.echo(f"Statement: {result.statement}")
    if result.evidence_quality:
        typer.echo(f"Evidence quality: {result.evidence_quality}")
    if result.details:
        for key in (
            "lesson",
            "why_it_matters",
            "next_questions",
            "prevention_actions",
            "recovery_actions",
            "confidence_limits",
        ):
            if key in result.details:
                typer.echo(f"{key}: {result.details[key]}")
    typer.echo(
        f"Supporting observation IDs: {', '.join(result.supporting_observation_ids) or '-'}"
    )
    typer.echo(f"Rejected observation IDs: {', '.join(result.rejected_observation_ids) or '-'}")
    if result.limitations:
        typer.echo("Limitations:")
        for limitation in result.limitations:
            typer.echo(f"- {limitation}")


@reflections_app.command("list")
def reflections_list(
    limit: Annotated[int, typer.Option(help="Maximum number of reflections to show.")] = 10,
) -> None:
    """List recent stored reflections."""
    from memoe.services.reflection_runner import list_reflections

    rows = list_reflections(limit=limit, settings=Settings())
    if not rows:
        typer.echo("No reflections found.")
        return

    for row in rows:
        typer.echo(
            " | ".join(
                [
                    row.created_at,
                    row.model_id,
                    row.reflection_type,
                    f"confidence={row.confidence}",
                    f"quality={row.evidence_quality_rating}",
                    row.statement,
                ]
            )
        )
