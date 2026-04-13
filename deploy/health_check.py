import sys
import traceback

try:
    import xmlrpc.client as xmlrpc_client
except ImportError:
    import xmlrpclib as xmlrpc_client

if __name__ == "__main__":
    try:
        server = xmlrpc_client.ServerProxy("http://localhost:9005/RPC2")
        info = server.supervisor.getAllProcessInfo()
        error_states = [process for process in info if process["state"] != 20]
        sys.exit(len(error_states))
    except Exception:
        traceback.print_exc()
        sys.exit(1)
