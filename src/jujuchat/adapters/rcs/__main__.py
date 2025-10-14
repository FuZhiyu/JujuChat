"""CLI entry point for RCS Adapter."""

import asyncio
import logging
import sys
from pathlib import Path

import typer
import uvicorn
from typing_extensions import Annotated

from .adapter import create_app
from .config import load_settings, Settings

app = typer.Typer(
    name="rcs-adapter",
    help="Twilio RCS to Claude Backend Adapter"
)


@app.command()
def run(
    host: Annotated[str, typer.Option("--host", "-h", help="Host to bind to")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="Port to bind to")] = 8811,
    log_level: Annotated[str, typer.Option("--log-level", help="Log level")] = "INFO",
    reload: Annotated[bool, typer.Option("--reload", help="Enable auto-reload for development")] = False,
    config: Annotated[Path, typer.Option("--config", "-c", help="Path to YAML config file")] = Path("rcs_config.yaml"),
):
    """Run the RCS adapter server."""
    
    # Set up logging
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    # Load settings
    try:
        settings = load_settings(config)
    except Exception as e:
        typer.echo(f"Error loading configuration: {e}", err=True)
        raise typer.Exit(1)
    
    # Create FastAPI app
    fastapi_app = create_app(settings)
    
    # Run server
    typer.echo(f"Starting RCS Adapter on {host}:{port}")
    typer.echo(f"Claude backend: {settings.claude_http_url}")
    typer.echo("Webhook endpoint: /twilio/rcs/***")
    
    try:
        uvicorn.run(
            fastapi_app,
            host=host,
            port=port,
            log_level=log_level.lower(),
            reload=reload
        )
    except KeyboardInterrupt:
        typer.echo("\nShutting down...")
    except Exception as e:
        typer.echo(f"Server error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def validate_config(
    config: Annotated[Path, typer.Option("--config", "-c", help="Path to YAML config file")] = Path("rcs_config.yaml"),
):
    """Validate configuration without starting the server."""
    try:
        settings = load_settings(config)
        typer.echo("✅ Configuration is valid")
        typer.echo(f"Twilio Account SID: {settings.twilio_account_sid[:8]}...")
        typer.echo(f"Claude backend URL: {settings.claude_http_url}")
        typer.echo(f"Attachments directory: {settings.attachments_dir.absolute()}")
        typer.echo(f"Max body size: {settings.adapter_max_body_bytes:,} bytes")
        typer.echo(f"Rate limit: {settings.adapter_rate_limit_rps} requests/second")
        if settings.twilio_messaging_service_sid:
            typer.echo(f"Messaging service SID: {settings.twilio_messaging_service_sid}")
        if settings.twilio_from_number:
            typer.echo(f"From number: {settings.twilio_from_number}")
        if settings.public_hostname:
            typer.echo(f"Public hostname: {settings.public_hostname}")
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
