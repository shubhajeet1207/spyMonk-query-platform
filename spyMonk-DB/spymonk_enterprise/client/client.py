"""
SpyMonk-DB Client SDK.

Simple client interface for interacting with SpyMonk-DB.
"""

from pathlib import Path
from typing import Optional, Iterator, Tuple, List, Dict
import logging
import socket
import grpc

from spymonk_enterprise.time.hybrid_clock import HybridLogicalClock, Timestamp
from spymonk_enterprise.storage.mvcc import MVCCStore
from spymonk_enterprise.transaction.transaction import TransactionManager, Transaction
from spymonk_enterprise.network.grpc.gen import spymonk_pb2
from spymonk_enterprise.network.grpc.gen import spymonk_pb2_grpc

logger = logging.getLogger(__name__)


class ReadOnlyTransactionError(RuntimeError):
    """Raised when a write is attempted inside a read-only transaction."""


class _AuthMetadataInterceptor(grpc.UnaryUnaryClientInterceptor,
                               grpc.UnaryStreamClientInterceptor):
    """Attaches a bearer auth token to every outgoing RPC."""

    def __init__(self, token: str):
        self._metadata = (("authorization", f"Bearer {token}"),)

    def _with_auth(self, details):
        metadata = list(details.metadata or []) + list(self._metadata)
        return details._replace(metadata=metadata)

    def intercept_unary_unary(self, continuation, details, request):
        return continuation(self._with_auth(details), request)

    def intercept_unary_stream(self, continuation, details, request):
        return continuation(self._with_auth(details), request)


def _build_channel(target: str, auth_token: str = "") -> grpc.Channel:
    channel = grpc.insecure_channel(target)
    if auth_token:
        channel = grpc.intercept_channel(channel, _AuthMetadataInterceptor(auth_token))
    return channel


class SpyMonkClient:
    """
    SpyMonk-DB Client.

    Main interface for interacting with the database.
    Supports both local (embedded) and remote (gRPC) modes.

    Example (Embedded):
        >>> client = SpyMonkClient("/tmp/spymonk")
        >>> client.start()

    Example (Remote):
        >>> client = SpyMonkClient("spymonk://localhost:50051")
        >>> client.start()
    """

    def __init__(
        self,
        connection_string: str,
        node_id: Optional[str] = None,
        auth_token: str = ""
    ):
        """
        Initialize client.

        Args:
            connection_string: Data directory (embedded) or spymonk:// URL (remote)
            node_id: Unique node identifier (default: hostname)
            auth_token: Bearer token sent with every RPC (remote mode only)
        """
        self.node_id = node_id or socket.gethostname()
        self.auth_token = auth_token

        if connection_string.startswith("spymonk://"):
            # Remote mode
            self.mode = "remote"
            self.target = connection_string.replace("spymonk://", "")
            self.channel = None
            self.stub = None
            logger.info(f"Initialized SpyMonk-DB remote client connecting to {self.target}")
        else:
            # Local embedded mode
            self.mode = "embedded"
            self.data_dir = Path(connection_string)
            self.data_dir.mkdir(parents=True, exist_ok=True)
            
            # Initialize components
            self.clock = HybridLogicalClock(self.node_id)
            self.store = MVCCStore(self.data_dir, self.clock)
            self.txn_manager = TransactionManager(self.clock, self.store)
            logger.info(f"Initialized SpyMonk-DB embedded client at {connection_string}")

    def start(self):
        """Start the client"""
        if self.mode == "remote":
            self.channel = _build_channel(self.target, self.auth_token)
            self.stub = spymonk_pb2_grpc.SpyMonkDBStub(self.channel)
            logger.info(f"Connected to remote SpyMonk-DB server at {self.target}")
        else:
            self.clock.start()
            logger.info("Started SpyMonk-DB embedded client")

    def stop(self):
        """Stop the client"""
        if self.mode == "remote":
            if self.channel:
                self.channel.close()
        else:
            self.clock.stop()
            self.store.close()
        logger.info(f"Stopped SpyMonk-DB {self.mode} client")

    # Internal helpers for gRPC conversion
    
    def _to_pb_timestamp(self, ts: Timestamp) -> spymonk_pb2.Timestamp:
        if ts is None: return spymonk_pb2.Timestamp()
        return spymonk_pb2.Timestamp(
            physical=ts.physical,
            logical=ts.logical,
            uncertainty_ns=ts.uncertainty_ns
        )

    # Simple KV operations (auto-commit)

    def put(self, key: bytes, value: bytes) -> Optional[Timestamp]:
        """Write key-value pair (auto-commit)."""
        if self.mode == "remote":
            request = spymonk_pb2.PutRequest(key=key, value=value)
            response = self.stub.Put(request)
            return response.timestamp if response.success else None
        else:
            return self.store.put(key, value)

    def get(self, key: bytes) -> Optional[bytes]:
        """Read value for key."""
        if self.mode == "remote":
            request = spymonk_pb2.GetRequest(key=key)
            response = self.stub.Get(request)
            return response.value if response.found else None
        else:
            return self.store.get(key)

    def delete(self, key: bytes) -> Optional[Timestamp]:
        """Delete key."""
        if self.mode == "remote":
            request = spymonk_pb2.DeleteRequest(key=key)
            response = self.stub.Delete(request)
            return response.timestamp if response.success else None
        else:
            return self.store.delete(key)

    def scan(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None
    ) -> Iterator[Tuple[bytes, bytes]]:
        """Range scan."""
        if self.mode == "remote":
            request = spymonk_pb2.ScanRequest(
                start_key=start_key or b"",
                end_key=end_key or b""
            )
            for response in self.stub.Scan(request):
                yield response.key, response.value
        else:
            yield from self.store.scan(start_key, end_key)

    # SQL Execution
    
    def execute_sql(self, sql: str) -> List[Dict[str, bytes]]:
        """Execute SQL query."""
        if self.mode == "remote":
            request = spymonk_pb2.ExecuteSQLRequest(sql=sql)
            response = self.stub.ExecuteSQL(request)
            results = []
            for row in response.rows:
                results.append(row.columns)
            return results
        else:
            # TODO: Local SQL execution if embedded
            raise NotImplementedError("Local SQL execution not yet implemented in client")

    # Transaction API

    def begin_transaction(self, read_only: bool = False) -> 'RemoteTransaction':
        """Begin a new transaction."""
        if self.mode == "remote":
            request = spymonk_pb2.BeginTransactionRequest(read_only=read_only)
            response = self.stub.BeginTransaction(request)
            return RemoteTransaction(self, response.transaction_id, read_only,
                                     stub=self.stub)
        else:
            return self.txn_manager.begin(read_only=read_only)


class RemoteTransaction:
    """Proxy for a transaction on a remote server.

    The transaction is pinned to the stub of the node it began on: a
    transaction's lock and buffer state live on that node, so its operations
    must never fail over to a different one.
    """

    def __init__(self, client, txn_id: str, read_only: bool, stub=None):
        self.client = client
        self.txn_id = txn_id
        self.read_only = read_only
        self.active = True
        self._stub = stub if stub is not None else client.stub

    def get(self, key: bytes) -> Optional[bytes]:
        request = spymonk_pb2.GetRequest(key=key, transaction_id=self.txn_id)
        response = self._stub.Get(request)
        return response.value if response.found else None

    def put(self, key: bytes, value: bytes):
        if self.read_only:
            raise ReadOnlyTransactionError("Cannot write in read-only transaction")
        request = spymonk_pb2.PutRequest(key=key, value=value, transaction_id=self.txn_id)
        self._stub.Put(request)

    def delete(self, key: bytes):
        if self.read_only:
            raise ReadOnlyTransactionError("Cannot delete in read-only transaction")
        request = spymonk_pb2.DeleteRequest(key=key, transaction_id=self.txn_id)
        self._stub.Delete(request)

    def commit(self) -> bool:
        request = spymonk_pb2.CommitTransactionRequest(transaction_id=self.txn_id)
        response = self._stub.CommitTransaction(request)
        self.active = False
        return response.success

    def abort(self):
        request = spymonk_pb2.AbortTransactionRequest(transaction_id=self.txn_id)
        self._stub.AbortTransaction(request)
        self.active = False

    # Context manager support: commit on clean exit, abort on exception.

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.active:
            if exc_type is None:
                self.commit()
            else:
                self.abort()
        return False


class DistributedClient:
    """
    Client for a multi-node SpyMonk-DB cluster.

    Auto-commit operations (put/get/delete/scan/execute_sql) and
    begin_transaction fail over across nodes on UNAVAILABLE errors.
    Transactions are pinned to the node they began on.

    Example:
        >>> client = DistributedClient(
        ...     nodes=["db1:50051", "db2:50051", "db3:50051"],
        ...     auth_token="shared-secret",
        ... )
        >>> client.start()
    """

    _FAILOVER_CODES = (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED)

    def __init__(
        self,
        nodes: List[str],
        auth_token: str = "",
        node_id: Optional[str] = None
    ):
        if not nodes:
            raise ValueError("DistributedClient requires at least one node address")
        self.nodes = [n.replace("spymonk://", "") for n in nodes]
        self.auth_token = auth_token
        self.node_id = node_id or socket.gethostname()
        self.mode = "distributed"
        self._channels: Dict[str, grpc.Channel] = {}
        self._stubs: Dict[str, spymonk_pb2_grpc.SpyMonkDBStub] = {}
        self._primary = 0

    def start(self):
        """Create channels/stubs for every node (gRPC connects lazily)."""
        for target in self.nodes:
            channel = _build_channel(target, self.auth_token)
            self._channels[target] = channel
            self._stubs[target] = spymonk_pb2_grpc.SpyMonkDBStub(channel)
        logger.info(f"Initialized distributed client for nodes: {self.nodes}")

    def stop(self):
        for channel in self._channels.values():
            channel.close()
        self._channels.clear()
        self._stubs.clear()
        logger.info("Stopped SpyMonk-DB distributed client")

    @property
    def stub(self):
        """Stub of the current primary node."""
        if not self._stubs:
            self.start()
        return self._stubs[self.nodes[self._primary]]

    def _call(self, op):
        """Run op(stub), failing over to the next node on UNAVAILABLE."""
        if not self._stubs:
            self.start()
        last_error = None
        for attempt in range(len(self.nodes)):
            idx = (self._primary + attempt) % len(self.nodes)
            stub = self._stubs[self.nodes[idx]]
            try:
                result = op(stub)
                self._primary = idx
                return result
            except grpc.RpcError as e:
                if e.code() in self._FAILOVER_CODES:
                    logger.warning(f"Node {self.nodes[idx]} unavailable, failing over")
                    last_error = e
                    continue
                raise
        raise last_error

    # Auto-commit KV operations (same surface as SpyMonkClient)

    def put(self, key: bytes, value: bytes):
        response = self._call(lambda s: s.Put(spymonk_pb2.PutRequest(key=key, value=value)))
        return response.timestamp if response.success else None

    def get(self, key: bytes) -> Optional[bytes]:
        response = self._call(lambda s: s.Get(spymonk_pb2.GetRequest(key=key)))
        return response.value if response.found else None

    def delete(self, key: bytes):
        response = self._call(lambda s: s.Delete(spymonk_pb2.DeleteRequest(key=key)))
        return response.timestamp if response.success else None

    def scan(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None
    ) -> Iterator[Tuple[bytes, bytes]]:
        """Range scan from the current primary (no mid-stream failover)."""
        request = spymonk_pb2.ScanRequest(
            start_key=start_key or b"",
            end_key=end_key or b""
        )
        for response in self.stub.Scan(request):
            yield response.key, response.value

    def execute_sql(self, sql: str) -> List[Dict[str, bytes]]:
        response = self._call(lambda s: s.ExecuteSQL(spymonk_pb2.ExecuteSQLRequest(sql=sql)))
        return [row.columns for row in response.rows]

    def begin_transaction(self, read_only: bool = False) -> RemoteTransaction:
        """Begin a transaction pinned to whichever node accepted it."""
        pinned = {}

        def begin(stub):
            response = stub.BeginTransaction(
                spymonk_pb2.BeginTransactionRequest(read_only=read_only))
            pinned["stub"] = stub
            return response

        response = self._call(begin)
        return RemoteTransaction(self, response.transaction_id, read_only,
                                 stub=pinned["stub"])
