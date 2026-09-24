import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_api_import_defers_dp01_adapter_module():
    code = "import sys; import bioaudit.api; assert 'bioaudit.dp01_adapter' not in sys.modules; assert callable(bioaudit.api.audit_dp01_filtering)"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, check=True)
