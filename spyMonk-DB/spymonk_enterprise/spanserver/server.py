"""
SpyMonk-DB SpanServer (Database Server).

Main gRPC server for SpyMonk-DB.
"""

import logging
import os
from concurrent import futures
import grpc
from pathlib import Path
from typing import Optional

from spymonk_enterprise.network.grpc.gen import spymonk_pb2
from spymonk_enterprise.network.grpc.gen import spymonk_pb2_grpc
from spymonk_enterprise.storage.mvcc import MVCCStore
from spymonk_enterprise.transaction.transaction import TransactionManager, Transaction, TransactionAborted
from spymonk_enterprise.time.hybrid_clock import HybridLogicalClock, Timestamp
from spymonk_enterprise.sql.executor.executor import QueryExecutor
from spymonk_enterprise.schema.schema import SchemaRegistry

logger = logging.getLogger(__name__)


class AuthInterceptor(grpc.ServerInterceptor):
    """Rejects RPCs that don't carry the configured bearer token."""

    def __init__(self, token: str):
        self._expected = f"Bearer {token}"

        def deny(request, context):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid or missing auth token")

        self._deny_handler = grpc.unary_unary_rpc_method_handler(deny)

    def intercept_service(self, continuation, handler_call_details):
        metadata = dict(handler_call_details.invocation_metadata)
        if metadata.get("authorization") == self._expected:
            return continuation(handler_call_details)
        return self._deny_handler

class SpyMonkDBService(spymonk_pb2_grpc.SpyMonkDBServicer):
    """gRPC service implementation for SpyMonk-DB"""

    def __init__(self, data_dir: str, node_id: str):
        self.data_dir = Path(data_dir)
        self.node_id = node_id
        
        # Initialize core components
        self.clock = HybridLogicalClock(node_id)
        self.clock.start()
        
        self.store = MVCCStore(self.data_dir, self.clock)
        self.txn_manager = TransactionManager(self.clock, self.store)
        self.schema_registry = SchemaRegistry()
        self.executor = QueryExecutor(self.store, self.schema_registry)
        
        logger.info(f"Initialized SpyMonkDBService on node {node_id}")

    def _to_pb_timestamp(self, ts: Timestamp) -> spymonk_pb2.Timestamp:
        return spymonk_pb2.Timestamp(
            physical=ts.physical,
            logical=ts.logical,
            uncertainty_ns=ts.uncertainty_ns
        )

    def _from_pb_timestamp(self, ts_pb: spymonk_pb2.Timestamp) -> Timestamp:
        return Timestamp(
            physical=ts_pb.physical,
            logical=ts_pb.logical,
            uncertainty_ns=ts_pb.uncertainty_ns
        )

    # Transaction operations

    def BeginTransaction(self, request, context):
        read_only = request.read_only
        txn = self.txn_manager.begin(read_only=read_only)
        
        return spymonk_pb2.BeginTransactionResponse(
            transaction_id=txn.txn_id,
            start_timestamp=self._to_pb_timestamp(txn.start_timestamp)
        )

    def CommitTransaction(self, request, context):
        txn_id = request.transaction_id
        txn = self.txn_manager.get_txn(txn_id)

        if not txn:
            context.abort(grpc.StatusCode.NOT_FOUND, f"Transaction {txn_id} not found")

        try:
            success = txn.commit()
        except TransactionAborted as e:
            logger.info(f"Transaction {txn_id} aborted on commit: {e}")
            return spymonk_pb2.CommitTransactionResponse(success=False)

        return spymonk_pb2.CommitTransactionResponse(
            success=success,
            commit_timestamp=self._to_pb_timestamp(txn.commit_timestamp) if success else None
        )

    def AbortTransaction(self, request, context):
        txn_id = request.transaction_id
        txn = self.txn_manager.get_txn(txn_id)

        if txn:
            txn.abort()
        
        return spymonk_pb2.AbortTransactionResponse(success=True)

    # Data operations

    def Get(self, request, context):
        txn_id = request.transaction_id
        ts = None
        if request.read_timestamp.physical > 0:
            ts = self._from_pb_timestamp(request.read_timestamp)
            
        if txn_id:
            txn = self.txn_manager.get_txn(txn_id)
            if not txn:
                context.abort(grpc.StatusCode.NOT_FOUND, f"Transaction {txn_id} not found")
            try:
                value = txn.get(request.key)
            except TransactionAborted as e:
                # RW txn's exclusive read lock acquisition can wound/timeout.
                logger.info(f"Transaction {txn_id} aborted on Get: {e}")
                return spymonk_pb2.GetResponse(value=b"", found=False)
        else:
            value = self.store.get(request.key, ts)

        return spymonk_pb2.GetResponse(
            value=value or b"",
            found=value is not None
        )

    def Put(self, request, context):
        txn_id = request.transaction_id
        if txn_id:
            txn = self.txn_manager.get_txn(txn_id)
            if not txn:
                context.abort(grpc.StatusCode.NOT_FOUND, f"Transaction {txn_id} not found")
            txn.put(request.key, request.value)
            return spymonk_pb2.PutResponse(success=True)
        else:
            ts = self.store.put(request.key, request.value)
            return spymonk_pb2.PutResponse(
                success=True,
                timestamp=self._to_pb_timestamp(ts)
            )

    def Delete(self, request, context):
        txn_id = request.transaction_id
        if txn_id:
            txn = self.txn_manager.get_txn(txn_id)
            if not txn:
                context.abort(grpc.StatusCode.NOT_FOUND, f"Transaction {txn_id} not found")
            txn.delete(request.key)
            return spymonk_pb2.DeleteResponse(success=True)
        else:
            ts = self.store.delete(request.key)
            return spymonk_pb2.DeleteResponse(
                success=True,
                timestamp=self._to_pb_timestamp(ts)
            )

    def Scan(self, request, context):
        txn_id = request.transaction_id
        start_key = request.start_key if request.start_key else None
        end_key = request.end_key if request.end_key else None
        ts = None
        if request.read_timestamp.physical > 0:
            ts = self._from_pb_timestamp(request.read_timestamp)
            
        # MVCCStore.scan yields (key, value)
        # We need to yield ScanResponse
        for key, value in self.store.scan(start_key, end_key, ts):
            yield spymonk_pb2.ScanResponse(key=key, value=value)

    # SQL operations

    def ExecuteSQL(self, request, context):
        # Implementation depends on a parser that can turn SQL string into AST
        # For now, let's assume a simplified execution
        from spymonk_enterprise.sql.parser.sql_parser import SQLParser
        
        parser = SQLParser()
        try:
            statement = parser.parse(request.sql)
            
            txn_id = request.transaction_id
            txn = None
            if txn_id:
                txn = self.txn_manager.get_txn(txn_id)
                if not txn:
                    context.abort(grpc.StatusCode.NOT_FOUND, f"Transaction {txn_id} not found")
            
            result = self.executor.execute(statement, txn)
            
            rows_pb = []
            for row in result.rows:
                # Convert dict to row with bytes
                cols = {k: str(v).encode() for k, v in row.items()}
                rows_pb.append(spymonk_pb2.QueryRow(columns=cols))
                
            return spymonk_pb2.ExecuteSQLResponse(
                rows=rows_pb,
                affected_rows=result.affected_rows
            )
        except ValueError as e:
            # Parse/validation errors describe the caller's own SQL — safe to return.
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
        except Exception:
            # Never leak internal error details to remote clients.
            logger.exception("Internal error executing SQL")
            context.abort(grpc.StatusCode.INTERNAL, "Internal server error")


class SpyMonkServer:
    """Main server class for SpanServer.

    Auth and TLS are configured via constructor args, falling back to the
    SPYMONK_DB_AUTH_TOKEN, SPYMONK_TLS_CERT and SPYMONK_TLS_KEY environment
    variables. Without a token the server logs a prominent warning: every
    RPC is then unauthenticated.
    """

    def __init__(self, host="127.0.0.1", port=50051, data_dir="data/spymonk",
                 node_id="node1", auth_token: Optional[str] = None,
                 tls_cert_path: Optional[str] = None,
                 tls_key_path: Optional[str] = None,
                 max_workers: int = 10):
        self.host = host
        self.port = port
        self.data_dir = data_dir
        self.node_id = node_id
        self.auth_token = auth_token if auth_token is not None \
            else os.getenv("SPYMONK_DB_AUTH_TOKEN", "")
        self.tls_cert_path = tls_cert_path or os.getenv("SPYMONK_TLS_CERT", "")
        self.tls_key_path = tls_key_path or os.getenv("SPYMONK_TLS_KEY", "")
        self.max_workers = max_workers
        self.server = None

    def start(self):
        interceptors = []
        if self.auth_token:
            interceptors.append(AuthInterceptor(self.auth_token))
        else:
            logger.warning("SPYMONK_DB_AUTH_TOKEN not set — server is UNAUTHENTICATED. "
                           "Do not expose this port beyond localhost.")

        self.server = grpc.server(
            futures.ThreadPoolExecutor(max_workers=self.max_workers),
            interceptors=interceptors,
        )

        service = SpyMonkDBService(self.data_dir, self.node_id)
        spymonk_pb2_grpc.add_SpyMonkDBServicer_to_server(service, self.server)

        listen_addr = f"{self.host}:{self.port}"
        if self.tls_cert_path and self.tls_key_path:
            with open(self.tls_key_path, "rb") as f:
                private_key = f.read()
            with open(self.tls_cert_path, "rb") as f:
                certificate = f.read()
            credentials = grpc.ssl_server_credentials([(private_key, certificate)])
            self.server.add_secure_port(listen_addr, credentials)
            logger.info(f"TLS enabled with certificate {self.tls_cert_path}")
        else:
            self.server.add_insecure_port(listen_addr)
            logger.warning("TLS not configured — traffic is unencrypted. "
                           "Set SPYMONK_TLS_CERT and SPYMONK_TLS_KEY for production.")

        self.server.start()
        logger.info(f"SpyMonk-DB Server started on {listen_addr}")
        return self.server

    def stop(self):
        if self.server:
            self.server.stop(0)
            logger.info("SpyMonk-DB Server stopped")
