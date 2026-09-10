import json
import re
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from rl_repro_harness.service import Workspace
from rl_repro_harness.web import make_server


class WebTests(unittest.TestCase):
    def test_api_session_and_artifact_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory) / ".meta")
            server = make_server(workspace, port=0)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(base) as response:
                    html = response.read().decode()
                token = re.search(r'name="harness-token" content="([^"]+)"', html).group(1)
                body = json.dumps({"path": directory}).encode()
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base + "/api/projects", data=body, headers={"Content-Type": "application/json"}))
                self.assertEqual(error.exception.code, 403)
                headers = {"Content-Type": "application/json", "X-Harness-Token": token}
                with urlopen(base + "/api/browse?path=" + directory) as response:
                    self.assertEqual(json.load(response)["path"], directory)
                with urlopen(Request(base + "/api/projects", data=body, headers=headers)) as response:
                    self.assertEqual(json.load(response)["path"], directory)
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base + "/api/projects", data=body, headers={**headers, "Origin": "http://untrusted.example"}))
                self.assertEqual(error.exception.code, 403)
                for path in ("/outputs/checkpoint.pt", "/../service.py", "/api/unknown"):
                    with self.assertRaises(HTTPError) as error:
                        urlopen(base + path)
                    self.assertEqual(error.exception.code, 404)
            finally:
                server.shutdown(); server.server_close(); thread.join(); workspace.close()
