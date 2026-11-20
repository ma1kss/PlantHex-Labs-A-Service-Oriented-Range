import ipaddress
import json
import os
import socket
from concurrent.futures import ThreadPoolExecutor

TIMEOUT = float(os.environ.get("TIMEOUT", "2.0"))


def probe(t):
    group, label, host, port = t
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(TIMEOUT)
    try:
        s.connect((host, int(port)))
        res = "open"
    except ConnectionRefusedError:
        res = "refused"
    except (socket.timeout, TimeoutError):
        res = "filtered"
    except OSError as e:  # EHOSTUNREACH / ENETUNREACH
        res = "filtered" if e.errno in (101, 110, 113) else f"error:{e.errno}"
    finally:
        s.close()
    return {"group": group, "label": label, "host": host, "port": int(port), "result": res}


def resolves(name):
    try:
        return socket.gethostbyname(name)
    except OSError:
        return None


targets = json.loads(os.environ.get("TARGETS", "[]"))
# ONLY_CIDRS: restrict to destinations inside these networks (e.g. the
# AllowedIPs of a VPN profile, so only tunnel-routed traffic is measured).
only = [ipaddress.ip_network(c.strip(), strict=False)
        for c in os.environ.get("ONLY_CIDRS", "").split(",") if c.strip()]
if only:
    targets = [t for t in targets if any(ipaddress.ip_address(t[2]) in n for n in only)]
names = json.loads(os.environ.get("NAMES", "[]"))
with ThreadPoolExecutor(max_workers=32) as ex:
    results = list(ex.map(probe, targets))
dns = {n: resolves(n) for n in names}
print(json.dumps({"results": results, "dns": dns}))
