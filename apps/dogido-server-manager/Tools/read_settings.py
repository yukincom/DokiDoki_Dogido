"""Read the endpoint and API credential through a private pipe using the existing settings loader."""
import ipaddress
import json
import os
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
os.chdir(root)
sys.path.insert(0, str(root))
from dogido_server.config import get_settings

settings = get_settings()
host = "127.0.0.1" if settings.bind_host == "localhost" else settings.bind_host
if not ipaddress.ip_address(host).is_loopback:
    raise SystemExit("loopback endpoint required")
host = f"[{host}]" if ":" in host else host
print(json.dumps({"base_url": f"http://{host}:{settings.bind_port}", "auth_token": settings.auth_token}))
