"""Pure API launcher. --check is offline and does not require ML dependencies."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading

if __package__:
    from .provider import APIProvider, load_config, gateway, search_handler
    from .runtime import configure_environment, search_sessions
else:
    from provider import APIProvider, load_config, gateway, search_handler
    from runtime import configure_environment, search_sessions

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.example.json'))
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--port', type=int, default=8790)
    args = parser.parse_args()
    provider = APIProvider(load_config(args.config))
    if args.check:
        print(json.dumps(provider.status(), ensure_ascii=False, indent=2))
        return
    if importlib.util.find_spec('jsonschema') is None:
        raise SystemExit('Install api_edition/requirements.txt in your own environment. No local model dependencies required.')
    bridge, token = gateway(provider)
    app, thread = None, None
    try:
        configure_environment(provider, 'http://127.0.0.1:' + str(bridge.server_port) + '/v1', token)
        os.environ['EV_API_PROFILE_STATUS'] = json.dumps(provider.status())
        sys.path.insert(0, str(ROOT / 'app'))
        import server
        server._tools = search_sessions(provider, server.db, server.CORPUS)
        app = server.ThreadingHTTPServer(('127.0.0.1', args.port), search_handler(server.Handler, provider))
        server.init()
        thread = threading.Thread(target=bridge.serve_forever, daemon=True)
        thread.start()
        print(json.dumps({'url': 'http://127.0.0.1:' + str(args.port), 'profile': provider.status(), 'gateway': 'private_loopback', 'model_training': False}), flush=True)
        app.serve_forever()
    finally:
        if app is not None:
            app.server_close()
        if thread is not None:
            bridge.shutdown()
            thread.join(timeout=2)
        bridge.server_close()


if __name__ == '__main__':
    main()
