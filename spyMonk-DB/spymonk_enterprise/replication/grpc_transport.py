"""
gRPC transport for Paxos: same message dataclasses as the in-process
transport, carried as msgpack envelopes over a single Send RPC.
"""

import dataclasses
import threading
from concurrent import futures
from typing import Any, Callable, Dict, Optional
import logging

import grpc
import msgpack

from spymonk_enterprise.network.grpc.gen import transport_pb2, transport_pb2_grpc
from spymonk_enterprise.replication.transport import MESSAGE_TYPES

logger = logging.getLogger(__name__)


def _encode(msg: Any) -> bytes:
    return msgpack.packb({"type": type(msg).__name__,
                          "fields": dataclasses.asdict(msg)})


def _decode(payload: bytes) -> Optional[Any]:
    obj = msgpack.unpackb(payload, raw=False, strict_map_key=False)
    cls = MESSAGE_TYPES.get(obj["type"])
    if cls is None:
        return None
    fields = obj["fields"]
    # msgpack round-trips SyncReply.accepted values as lists; coerce to tuples,
    # and integer dict keys survive via strict_map_key=False.
    if cls.__name__ == "SyncReply":
        fields["accepted"] = {int(k): tuple(v) for k, v in fields["accepted"].items()}
    return cls(**fields)


class _Servicer(transport_pb2_grpc.PaxosTransportServicer):
    def __init__(self, owner: 'GrpcPeerTransport'):
        self._owner = owner

    def Send(self, request, context):
        msg = _decode(request.payload)
        reply = self._owner._handler(msg) if (msg is not None and self._owner._handler) else None
        payload = _encode(reply) if reply is not None else b""
        return transport_pb2.TransportEnvelope(payload=payload)


class GrpcReplicationServer:
    """Standalone gRPC host for the PaxosTransport service.

    Owns only the receive side (register/start/stop) with no knowledge of
    peers or how to dial out -- the server-side half that GrpcPeerTransport
    below composes with an outbound stub pool. Useful when a process wants
    to receive Paxos messages without holding a peer address map.
    """

    def __init__(self, listen_addr: str):
        self.listen_addr = listen_addr
        self._handler: Optional[Callable] = None
        self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
        transport_pb2_grpc.add_PaxosTransportServicer_to_server(_Servicer(self), self._server)
        self._server.add_insecure_port(listen_addr)

    def register(self, handler: Callable[[Any], Any]) -> None:
        self._handler = handler

    def start(self) -> None:
        self._server.start()

    def stop(self) -> None:
        self._server.stop(grace=0.5)


class GrpcPeerTransport:
    """register/send-compatible with BoundTransport, over gRPC."""

    def __init__(self, replica_id: str, listen_addr: str, peers: Dict[str, str]):
        self.replica_id = replica_id
        self.listen_addr = listen_addr
        self.peers = peers
        self._handler: Optional[Callable] = None
        self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
        transport_pb2_grpc.add_PaxosTransportServicer_to_server(_Servicer(self), self._server)
        self._server.add_insecure_port(listen_addr)
        self._stubs: Dict[str, transport_pb2_grpc.PaxosTransportStub] = {}
        self._channels = {}
        self._lock = threading.Lock()

    def register(self, handler: Callable[[Any], Any]) -> None:
        self._handler = handler

    def start(self) -> None:
        self._server.start()

    def stop(self) -> None:
        self._server.stop(grace=0.5)
        for ch in self._channels.values():
            ch.close()

    def send(self, to: str, msg: Any) -> Optional[Any]:
        if to == self.replica_id and self._handler:
            return self._handler(msg)   # loopback without a network hop
        stub = self._stub_for(to)
        if stub is None:
            return None
        try:
            envelope = transport_pb2.TransportEnvelope(payload=_encode(msg))
            reply = stub.Send(envelope, timeout=2.0)
            return _decode(reply.payload) if reply.payload else None
        except grpc.RpcError as e:
            logger.warning(f"{self.replica_id} -> {to} RPC failed: {e.code()}")
            return None

    def _stub_for(self, to: str):
        with self._lock:
            if to not in self._stubs:
                addr = self.peers.get(to)
                if addr is None:
                    return None
                channel = grpc.insecure_channel(addr)
                self._channels[to] = channel
                self._stubs[to] = transport_pb2_grpc.PaxosTransportStub(channel)
            return self._stubs[to]
