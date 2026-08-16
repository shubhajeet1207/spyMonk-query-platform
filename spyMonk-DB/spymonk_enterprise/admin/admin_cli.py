"""
SpyMonk-DB Admin CLI.

Tools for monitoring and managing the database.
"""

import click
from rich.console import Console
from rich.table import Table

from spymonk_enterprise.client.client import SpyMonkClient

console = Console()

@click.group()
@click.option('--connect', default='spymonk://localhost:50051', help='Connection string')
@click.pass_context
def cli(ctx, connect):
    """SpyMonk-DB Administration Tool"""
    ctx.ensure_object(dict)
    ctx.obj['connect'] = connect

@cli.command()
@click.pass_context
def status(ctx):
    """Check database status"""
    connect = ctx.obj['connect']
    console.print(f"Checking status of [green]{connect}[/green]...")
    try:
        client = SpyMonkClient(connect)
        client.start()
        # Ping the server with a simple Get
        client.get(b"__ping__")
        console.print("[bold green]Online[/bold green]")
        client.stop()
    except Exception as e:
        console.print(f"[bold red]Offline:[/bold red] {e}")

@cli.command()
@click.pass_context
def list_tables(ctx):
    """List all tables (not yet fully implemented in SpanServer)"""
    connect = ctx.obj['connect']
    client = SpyMonkClient(connect)
    client.start()
    # This would normally call a metadata RPC
    console.print("[yellow]Feature coming soon: Global Schema Registry metadata via gRPC[/yellow]")
    client.stop()

if __name__ == '__main__':
    cli(obj={})
