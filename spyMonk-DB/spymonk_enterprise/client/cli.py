"""
SpyMonk-DB CLI Shell.

Interactive REPL for interacting with SpyMonk-DB.
"""

import click
import logging
import sys
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import print as rprint

from spymonk_enterprise.client.client import SpyMonkClient

# Configure logging to be less chatty in CLI
logging.basicConfig(level=logging.ERROR)

console = Console()

@click.command()
@click.option('--connect', default='data/spymonk', help='Connection string (dir or spymonk://host:port)')
def main(connect):
    """SpyMonk-DB Interactive CLI"""
    
    rprint(Panel.fit(
        "[bold cyan]SpyMonk-DB Interactive Shell[/bold cyan]\n"
        f"Connected to: [green]{connect}[/green]",
        title="Welcome", border_style="blue"
    ))

    try:
        client = SpyMonkClient(connect)
        client.start()
    except Exception as e:
        console.print(f"[bold red]Error connecting to database:[/bold red] {e}")
        sys.exit(1)

    try:
        while True:
            cmd_line = console.input("[bold green]spymonk> [/bold green]").strip()
            
            if not cmd_line:
                continue
                
            if cmd_line.lower() in ('exit', 'quit', '\\q'):
                break
            
            # Handle SQL
            if cmd_line.lower().startswith(('select', 'insert', 'update', 'delete', 'create')):
                try:
                    results = client.execute_sql(cmd_line)
                    if results:
                        table = Table(show_header=True, header_style="bold magenta")
                        # Get columns from first row
                        for col in results[0].keys():
                            table.add_column(col)
                        
                        for row in results:
                            table.add_row(*[v.decode() for v in row.values()])
                        
                        console.print(table)
                    else:
                        console.print("[yellow]Query executed successfully (no rows returned).[/yellow]")
                except Exception as e:
                    console.print(f"[bold red]SQL Error:[/bold red] {e}")
            
            # Simple KV helper commands
            elif cmd_line.startswith("put "):
                parts = cmd_line.split(" ", 2)
                if len(parts) < 3:
                    console.print("[red]Usage: put <key> <value>[/red]")
                    continue
                client.put(parts[1].encode(), parts[2].encode())
                console.print(f"[green]OK[/green]")
                
            elif cmd_line.startswith("get "):
                parts = cmd_line.split(" ")
                if len(parts) < 2:
                    console.print("[red]Usage: get <key>[/red]")
                    continue
                val = client.get(parts[1].encode())
                if val:
                    console.print(val.decode())
                else:
                    console.print("[yellow]Not found[/yellow]")
            
            else:
                console.print(f"[red]Unknown command: {cmd_line}[/red]")
                
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
    finally:
        client.stop()
        rprint("[blue]Goodbye![/blue]")

if __name__ == '__main__':
    main()
