"""
CLI entry point for SpyMonk-DB Server.
"""

import click
import logging
import signal
import sys
from spymonk_enterprise.spanserver.server import SpyMonkServer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("spymonk-server")

@click.command()
@click.option('--host', default='127.0.0.1', help='Host to listen on')
@click.option('--port', default=50051, help='Port to listen on')
@click.option('--data-dir', default='data/spymonk', help='Directory for database files')
@click.option('--node-id', default='node-1', help='Unique node identifier')
def main(host, port, data_dir, node_id):
    """Start SpyMonk-DB Server"""
    
    server = SpyMonkServer(host=host, port=port, data_dir=data_dir, node_id=node_id)
    
    def handle_signal(signum, frame):
        logger.info("Received termination signal, stopping server...")
        server.stop()
        sys.exit(0)
        
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    
    logger.info(f"Starting SpyMonk-DB Server on {host}:{port}...")
    logger.info(f"Data directory: {data_dir}")
    logger.info(f"Node ID: {node_id}")
    
    server.start()

    # Block until the server terminates (no busy-poll)
    try:
        server.server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop()

if __name__ == '__main__':
    main()
