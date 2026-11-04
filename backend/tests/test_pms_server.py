"""The single-process deployment entry point (pms_server.py) and PMS_UPLOAD_DIR.

No database is involved: every request here is answered before the
application reaches MongoDB (static files, the OpenAPI document, a
trailing-slash redirect), and lifespan is not started, so no startup code
runs against any database.
"""

import importlib
import os
import subprocess
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PmsServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dist = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(cls.dist.name, "assets"))
        with open(os.path.join(cls.dist.name, "index.html"), "w") as f:
            f.write("<html>SPA</html>")
        with open(os.path.join(cls.dist.name, "assets", "a.js"), "w") as f:
            f.write("console.log(1)")
        cls.previous = os.environ.get("PMS_FRONTEND_DIST")
        os.environ["PMS_FRONTEND_DIST"] = cls.dist.name
        sys.modules.pop("pms_server", None)
        sys.path.insert(0, BACKEND_DIR)
        cls.module = importlib.import_module("pms_server")
        cls.client = TestClient(cls.module.app, follow_redirects=False)

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("pms_server", None)
        if cls.previous is None:
            os.environ.pop("PMS_FRONTEND_DIST", None)
        else:
            os.environ["PMS_FRONTEND_DIST"] = cls.previous
        cls.dist.cleanup()

    def test_frontend_files_and_spa_fallback(self):
        self.assertEqual(self.client.get("/").text, "<html>SPA</html>")
        self.assertEqual(self.client.get("/employees/123").text, "<html>SPA</html>")
        self.assertEqual(self.client.get("/assets/a.js").text, "console.log(1)")

    def test_missing_asset_is_a_404_not_html_or_500(self):
        self.assertEqual(self.client.get("/assets/missing.js").status_code, 404)

    def test_api_prefix_reaches_the_application(self):
        response = self.client.get("/api/openapi.json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/auth/login", response.json()["paths"])

    def test_api_redirect_keeps_the_prefix(self):
        response = self.client.get("/api/documents")
        self.assertEqual(response.status_code, 307)
        self.assertTrue(response.headers["location"].endswith("/api/documents/"))

    def test_path_that_only_starts_with_api_is_a_frontend_route(self):
        self.assertEqual(self.client.get("/apix").text, "<html>SPA</html>")

    def test_missing_frontend_build_is_refused(self):
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); import pms_server"
        )
        env = {**os.environ, "PMS_FRONTEND_DIST": os.path.join(self.dist.name, "nope")}
        result = subprocess.run(
            [sys.executable, "-I", "-c", code, BACKEND_DIR],
            env=env, capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PMS_FRONTEND_DIST", result.stderr)


class UploadDirSettingTests(unittest.TestCase):
    def _upload_dir(self, value):
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "from app import storage; print(storage.UPLOAD_DIR)"
        )
        env = {**os.environ}
        env.pop("PMS_UPLOAD_DIR", None)
        if value is not None:
            env["PMS_UPLOAD_DIR"] = value
        return subprocess.run(
            [sys.executable, "-I", "-c", code, BACKEND_DIR],
            env=env, capture_output=True, text=True,
        )

    def test_default_is_app_uploads(self):
        result = self._upload_dir(None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            os.path.normcase(result.stdout.strip()),
            os.path.normcase(os.path.join(BACKEND_DIR, "app", "uploads")),
        )

    def test_absolute_path_is_used_and_created(self):
        with tempfile.TemporaryDirectory() as root:
            target = os.path.join(root, "nested", "uploads")
            result = self._upload_dir(target)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(os.path.normcase(result.stdout.strip()), os.path.normcase(target))
            self.assertTrue(os.path.isdir(target))

    def test_relative_path_is_refused(self):
        result = self._upload_dir("relative/uploads")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PMS_UPLOAD_DIR", result.stderr)


if __name__ == "__main__":
    unittest.main()
