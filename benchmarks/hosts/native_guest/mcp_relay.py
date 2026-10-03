import json
import os
import pathlib
import socket
import struct
import sys
import time

sys.path.insert(0, "/opt")
import linux_mcp_socket_transport as transport

hostwork = pathlib.Path(sys.argv[1])
mcpwork = pathlib.Path(sys.argv[2])
result = {}
try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(hostwork / "relay.sock"))
        os.chown(hostwork / "relay.sock", 1000, 1000)
        os.chmod(hostwork / "relay.sock", 0o600)
        listener.listen(1)
        listener.settimeout(30)
        client, _ = listener.accept()
        peer = struct.unpack("3i", client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if peer[1] != 1000:
            raise ValueError("host_peer_uid")
        deadline = time.monotonic() + 10
        while not (mcpwork / "mcp.sock").exists():
            if time.monotonic() > deadline:
                raise TimeoutError("mcp_endpoint_timeout")
            time.sleep(0.05)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.settimeout(5)
        server.connect(str(mcpwork / "mcp.sock"))
        peer = struct.unpack("3i", server.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if peer[1] != 1001:
            raise ValueError("mcp_peer_uid")
        server.settimeout(None)
        transport.relay_bidirectional(client, server, max_total_bytes=1048576, timeout_seconds=30)
        result = {"peer_uids_verified": [1000, 1001], "failure": None}
except Exception as e:
    result = {"failure": type(e).__name__, "code": getattr(e, "code", None)}
pathlib.Path("/opt/mcp-relay-result.json").write_text(json.dumps(result))
sys.exit(0 if result["failure"] is None else 1)
