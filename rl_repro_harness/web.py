"""Loopback-only HTTP workbench for project execution and metadata."""
from __future__ import annotations

import errno
import hmac
import json
import mimetypes
import secrets
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .service import Workspace
from .commands import build_commands, describe_command


def make_server(workspace, host="127.0.0.1", port=8765):
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("The execution workbench must bind to localhost")
    token = secrets.token_urlsafe(32)
    static = Path(__file__).parent / "static"

    class Handler(BaseHTTPRequestHandler):
        def send(self, status, body, content_type="application/json"):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def trusted_host(self):
            port = self.server.server_port
            return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def do_GET(self):
            if not self.trusted_host():
                return self.send(403, {"error": "Invalid host"})
            path = urlsplit(self.path).path
            if path == "/api/state":
                return self.send(200, workspace.state())
            if path == "/api/resources":
                return self.send(200, workspace.resources())
            if path == "/api/outputs":
                try:
                    project_id = parse_qs(urlsplit(self.path).query).get("project_id", [""])[0]
                    return self.send(200, workspace.project_outputs(project_id))
                except (ValueError, OSError) as exc:
                    return self.send(400, {"error": str(exc)})
            if path == "/api/record":
                try:
                    execution_id = parse_qs(urlsplit(self.path).query).get("execution_id", [""])[0]
                    return self.send(200, workspace.record(execution_id))
                except (ValueError, OSError) as exc:
                    return self.send(404, {"error": str(exc)})
            if path == "/api/llm/config":
                try:
                    return self.send(200, workspace.llm_config())
                except ValueError as exc:
                    return self.send(500, {"error": str(exc)})
            if path == "/api/catalog":
                try:
                    project_id = parse_qs(urlsplit(self.path).query).get("project_id", [""])[0]
                    return self.send(200, workspace.catalog(project_id))
                except (ValueError, IndexError) as exc:
                    return self.send(400, {"error": str(exc)})
            if path == "/api/environment":
                try:
                    query = parse_qs(urlsplit(self.path).query)
                    return self.send(200, workspace.environment(query.get("project_id", [""])[0], query.get("python", [None])[0], query.get("working_directory", [None])[0]))
                except (ValueError, IndexError, OSError) as exc:
                    return self.send(400, {"error": str(exc)})
            if path == "/api/defaults":
                try:
                    project_id = parse_qs(urlsplit(self.path).query).get("project_id", [""])[0]
                    return self.send(200, workspace.defaults(project_id))
                except (ValueError, IndexError) as exc:
                    return self.send(400, {"error": str(exc)})
            if path == "/api/browse":
                try:
                    query = parse_qs(urlsplit(self.path).query)
                    python_picker = query.get('kind', [''])[0] == 'python'
                    root = Path(workspace.project(query.get('project_id', [''])[0])['path']) if python_picker else None
                    requested = query.get("path", [str(root or Path.cwd())])[0]
                    directory = Path(requested).expanduser().resolve()
                    if not directory.is_dir():
                        raise ValueError("Directory does not exist")
                    if root and directory != root and root not in directory.parents:
                        raise ValueError('File picker must stay inside Project Root')
                    entries = []
                    for child in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
                        if not child.is_symlink() and not child.name.startswith(".") and (child.is_dir() or python_picker and child.suffix == '.py' and child.is_file()):
                            entries.append({"name": child.name, "path": str(child), 'kind': 'directory' if child.is_dir() else 'file'})
                    parent = str(directory.parent) if directory != directory.parent and directory != root else None
                    return self.send(200, {"path": str(directory), "parent": parent, "entries": entries})
                except (ValueError, OSError) as exc:
                    return self.send(400, {"error": str(exc)})
            assets = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/lucide.js": "lucide.js", "/cartpole.png": "cartpole.png"}
            if path not in assets:
                return self.send(404, {"error": "Not found"})
            file = static / assets[path]
            if not file.is_file():
                return self.send(404, {"error": "Asset not found"})
            body = file.read_bytes()
            if path == "/":
                body = body.replace(b"__CSRF_TOKEN__", token.encode())
            self.send(200, body, mimetypes.guess_type(file.name)[0] or "application/octet-stream")

        def do_POST(self):
            origin = self.headers.get("Origin")
            if not self.trusted_host() or (origin and origin != "http://" + self.headers.get("Host", "")) or not hmac.compare_digest(self.headers.get("X-Harness-Token", ""), token):
                return self.send(403, {"error": "Invalid local session"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 65_536 or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("Expected a JSON body up to 64 KiB")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("Expected an object")
                path = urlsplit(self.path).path
                if path == "/api/projects":
                    result = workspace.register(data["path"], data.get("name"))
                elif path == '/api/environment/prepare':
                    result = workspace.prepare_project_environment(data['project_id'], data.get('python'), data.get('working_directory'))
                elif path == "/api/llm/config":
                    result = workspace.configure_llm(data)
                elif path == "/api/llm/test":
                    result = workspace.test_llm(data or None)
                elif path == "/api/analyze":
                    result = workspace.analyze(data["project_id"], data.get("command", ""), data.get("llm", False))
                elif path == '/api/analyze-file':
                    result = workspace.analyze_file(data['project_id'], data['path'], data['kind'], data.get('entry_id'))
                elif path == '/api/resources/policy':
                    result = workspace.configure_resources(data)
                elif path == "/api/launch":
                    ids = workspace.launch(data)
                    result = {"execution_ids": ids, 'executions': [workspace.record(i) for i in ids]}
                elif path == '/api/command':
                    result = describe_command(data['command']) if data.get('describe') else (workspace.command_plan(data) if data.get('project_id') else build_commands(data))
                elif path == "/api/cancel":
                    workspace.cancel(data["execution_id"])
                    result = {"status": "cancelling"}
                elif path == "/api/start":
                    result = workspace.start_execution(data["execution_id"])
                elif path == "/api/pause":
                    result = workspace.pause(data["execution_id"])
                elif path == "/api/resume":
                    result = workspace.resume(data["execution_id"])
                elif path == "/api/delete":
                    result = workspace.delete_records(data.get("execution_ids", [data.get("execution_id")]))
                elif path == "/api/open-output":
                    result = {"path": workspace.open_output(data["execution_id"])}
                else:
                    return self.send(404, {"error": "Not found"})
                self.send(200, result)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                self.send(400, {"error": str(exc)})
            except Exception:
                self.send(500, {"error": "Request failed; check the server console"})
                import traceback
                traceback.print_exc()

        def log_message(self, *_):
            pass

    return ThreadingHTTPServer((host, port), Handler)


def serve(metadata_root=".harness", host="127.0.0.1", port=8765, projects=()):
    workspace = Workspace(metadata_root)
    server = None
    try:
        for project in projects:
            workspace.register(project)
        for candidate in range(port, port + 20):
            try:
                server = make_server(workspace, host, candidate)
                break
            except OSError as exc:
                if exc.errno != errno.EADDRINUSE:
                    raise
        if server is None:
            raise ValueError("No free local port found")
        print(f"RL workbench: http://{host}:{server.server_port}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server:
            server.server_close()
        workspace.close()
