import sys

sys.path.insert(0, "/runtime")
import transport

transport.host_stdio_client("/work/relay.sock")
