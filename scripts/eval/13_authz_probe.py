from __future__ import annotations

import base64
import json
import os
import sys
import time
import uuid

import httpx
from jose import jwt

EDGE = os.environ.get("EDGE", "https://127.0.0.1:8006")
DIRECT = {
    "iam":           "http://sor_iam:8001",
    "scenario":      "http://sor_scenario:8002",
    "events":        "http://sor_events:8004",
    "monitoring":    "http://sor_monitoring:8005",
    "data":          "http://sor_data:8007",
    "learning":      "http://sor_learning:8008",
    "orchestration": "http://sor_orchestration:8009",
}
X = str(uuid.uuid4())          # dummy object id → 404 when allowed
BAD_BODY = []                  # valid JSON, invalid model → 422 when allowed

ANY, STAFF, ADMIN = "any", "staff", "admin"

# (method, edge path, required role, send body?)  — inventory of every
# protected route in services/*/routes.py (public login/register excluded).
ROUTES = [
    # IAM (F01/F02)
    ("GET",    "/iam/auth/validate",                    ANY,   False),
    ("POST",   "/iam/auth/change-password",             ANY,   True),
    ("GET",    "/iam/profile",                          ANY,   False),
    ("PUT",    "/iam/profile",                          ANY,   True),
    ("GET",    f"/iam/profiles/{X}",                    ANY,   False),
    ("GET",    "/iam/settings/registration-secret",     ADMIN, False),
    ("PUT",    "/iam/settings/registration-secret",     ADMIN, True),
    ("POST",   "/iam/access/grant",                     ANY,   True),
    ("POST",   "/iam/access/revoke",                    ANY,   True),
    ("GET",    "/iam/users",                            STAFF, False),
    ("DELETE", f"/iam/users/{X}",                       ADMIN, False),
    ("PUT",    f"/iam/users/{X}/roles",                 ADMIN, True),
    ("POST",   f"/iam/users/{X}/sessions/revoke",       ADMIN, False),
    ("GET",    "/iam/settings/security",                ADMIN, False),
    ("PUT",    "/iam/settings/security",                ADMIN, True),
    # Scenario Management (F03/F05)
    ("POST",   "/scenario/challenges",                  STAFF, True),
    ("POST",   "/scenario/challenges/stream",           STAFF, True),
    ("PUT",    f"/scenario/challenges/{X}",             STAFF, True),
    ("DELETE", f"/scenario/challenges/{X}",             STAFF, False),
    ("GET",    "/scenario/challenges",                  ANY,   False),
    ("POST",   f"/scenario/challenges/{X}/visibility",  STAFF, True),
    ("GET",    "/scenario/challenges/stats",            STAFF, False),
    ("GET",    f"/scenario/challenges/{X}/flag",        ADMIN, False),
    ("GET",    f"/scenario/challenges/{X}/download",    ANY,   False),
    ("GET",    "/scenario/my-solves",                   ANY,   False),
    ("DELETE", f"/scenario/submissions?challenge_id={X}&user_id={X}", STAFF, False),
    ("POST",   f"/scenario/challenges/{X}/submit",      ANY,   True),
    ("POST",   f"/scenario/challenges/{X}/session",     ANY,   False),
    ("GET",    "/scenario/sessions",                    ANY,   False),
    ("DELETE", f"/scenario/sessions/{X}",               ANY,   False),
    ("GET",    f"/scenario/sessions/{X}/wireguard.conf", ANY,  False),
    ("GET",    "/scenario/docker/status",               STAFF, False),
    ("GET",    "/scenario/events",                      ANY,   False),
    ("POST",   "/scenario/events",                      STAFF, True),
    ("GET",    f"/scenario/events/{X}",                 ANY,   False),
    ("PUT",    f"/scenario/events/{X}",                 STAFF, True),
    ("DELETE", f"/scenario/events/{X}",                 STAFF, False),
    # Event & Notification (F13)
    ("GET",    "/events/announcements",                 ANY,   False),
    ("POST",   "/events/announcements",                 STAFF, True),
    ("PUT",    f"/events/announcements/{X}",            STAFF, True),
    ("DELETE", f"/events/announcements/{X}",            STAFF, False),
    # Monitoring & Analytics (F08/F09)
    ("GET",    "/monitoring/info",                      STAFF, False),
    ("GET",    "/monitoring/targets",                   STAFF, False),
    ("GET",    "/monitoring/summary",                   STAFF, False),
    ("GET",    "/monitoring/query",                     ADMIN, False),
    ("POST",   "/monitoring/observations",              ANY,   True),
    ("DELETE", f"/monitoring/observations/{X}",         ANY,   False),
    ("GET",    "/monitoring/observations",              STAFF, False),
    ("GET",    "/monitoring/alerts",                    STAFF, False),
    # Data & Logging (F11/F12)
    ("POST",   "/data/ingest",                          ADMIN, True),
    ("GET",    "/data/integrity",                       STAFF, False),
    ("GET",    "/data/logs",                            STAFF, False),
    ("GET",    "/data/stats",                           STAFF, False),
    ("GET",    "/data/logs/export",                     ADMIN, False),
    ("DELETE", "/data/logs",                            ADMIN, True),
    ("DELETE", f"/data/logs/{X}",                       ADMIN, False),
    # Learning & Progress (F07/F10)
    ("GET",    "/learning/me",                          ANY,   False),
    ("GET",    "/learning/leaderboard",                 ANY,   False),
    ("POST",   "/learning/participants",                STAFF, True),
    ("POST",   "/learning/attempts",                    ANY,   True),
    ("POST",   "/learning/attempts/end",                ANY,   True),
    ("GET",    "/learning/participants",                STAFF, False),
    ("DELETE", f"/learning/participants/{X}",           STAFF, False),
    ("PUT",    f"/learning/participants/{X}/team",      STAFF, True),
    ("GET",    "/learning/teams",                       ANY,   False),
    ("POST",   "/learning/teams",                       STAFF, True),
    ("PUT",    f"/learning/teams/{X}",                  STAFF, True),
    ("DELETE", f"/learning/teams/{X}",                  STAFF, False),
    ("GET",    f"/learning/teams/{X}/solves",           STAFF, False),
    ("GET",    "/learning/leaderboard/teams",           ANY,   False),
    ("GET",    f"/learning/participants/{X}/solves",    STAFF, False),
    ("POST",   "/learning/solves/revoke",               STAFF, True),
    ("GET",    "/learning/stats",                       STAFF, False),
    ("POST",   f"/learning/exercises/{X}/enrolments",   STAFF, True),
    ("GET",    f"/learning/exercises/{X}/enrolments",   STAFF, False),
    ("DELETE", f"/learning/exercises/{X}/enrolments/{X}", STAFF, False),
    ("GET",    "/learning/me/exercises",                ANY,   False),
    ("POST",   "/learning/maintenance/reconcile",       ADMIN, True),
    # Orchestration (F06)
    ("POST",   "/orchestration/launches",               ANY,   True),
    ("DELETE", f"/orchestration/sessions/{X}",          ANY,   False),
    ("GET",    "/orchestration/launches",               STAFF, False),
]

ALLOWED = {
    "player":     {ANY},
    "instructor": {ANY, STAFF},
    "admin":      {ANY, STAFF, ADMIN},
}

ALG = "RS256"
ISS = "cyber-range-iam"
PRIV = os.environ.get("JWT_PRIVATE_PEM", "")
PUB = open("/run/keys/jwt_public.pem").read()
SVC_KEY = open("/run/secrets/evt_signing").read()     # this container's Ed25519 key


def b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).rstrip(b"=").decode()


def claims(roles, *, exp_delta=3600, iss=ISS):
    now = int(time.time())
    return {"sub": str(uuid.uuid4()), "jti": str(uuid.uuid4()), "preferred_username": "probe-forged",
            "email": "", "realm_access": {"roles": roles}, "iss": iss,
            "iat": now - 10, "exp": now + exp_delta}


def tampered(real: str) -> str:
    """Real player token with payload rewritten to admin, signature kept."""
    h, p, s = real.split(".")
    pad = "=" * (-len(p) % 4)
    body = json.loads(base64.urlsafe_b64decode(p + pad))
    body["realm_access"] = {"roles": ["admin"]}
    return f"{h}.{b64(body)}.{s}"


def hs256_with(key: str, c: dict) -> str:
    """HMAC-signed token built by hand (python-jose refuses PEM keys as HMAC
    secrets) — the classic RS256→HS256 'algorithm confusion' forgery."""
    import hashlib, hmac
    h, p = b64({"alg": "HS256", "typ": "JWT"}), b64(c)
    sig = hmac.new(key.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}." + base64.urlsafe_b64encode(sig).rstrip(b"=").decode()


def forged_variants(player_tok: str) -> dict[str, str | None]:
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    v = {
        "missing":              None,
        "malformed":            "not.a.jwt",
        "tampered payload":     tampered(player_tok),
        "attacker RSA key":     jwt.encode(claims(["admin"]), attacker, algorithm="RS256"),
        "alg=none":             f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(claims(['admin']))}.",
        "HS256 w/ public key":  hs256_with(PUB, claims(["admin"])),
        "service event key":    jwt.encode(claims(["admin"]), SVC_KEY, algorithm="EdDSA")
                                if hasattr(jwt, "ALGORITHMS") and "EdDSA" in getattr(jwt.ALGORITHMS, "SUPPORTED", ()) else
                                hs256_with(SVC_KEY, claims(["admin"])),
    }
    if PRIV:   # valid signature, invalid claims
        v["expired (valid sig)"] = jwt.encode(claims(["admin"], exp_delta=-60), PRIV, algorithm=ALG)
        v["wrong issuer"]        = jwt.encode(claims(["admin"], iss="evil-issuer"), PRIV, algorithm=ALG)
    return v


def call(cli: httpx.Client, base: str, method: str, path: str, tok, body: bool) -> int:
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    kw = {"json": BAD_BODY} if body else {}
    try:
        r = cli.request(method, base + path, headers=headers, timeout=20, **kw)
        return r.status_code
    except Exception:
        return -1


def direct_url(path: str) -> tuple[str, str]:
    svc, rest = path.lstrip("/").split("/", 1)
    return DIRECT[svc], "/" + rest


def main() -> int:
    toks = {"player": os.environ["TOK_PLAYER"],
            "instructor": os.environ["TOK_INSTRUCTOR"],
            "admin": os.environ["TOK_ADMIN"]}
    out = {"routes": len(ROUTES), "role_matrix": {}, "forged": {}, "violations": []}
    with httpx.Client(verify=False) as cli:
        # 1. role matrix, edge + direct
        for path_kind in ("edge", "direct"):
            ok = bad = 0
            for (m, p, need, body) in ROUTES:
                base, rp = (EDGE, p) if path_kind == "edge" else direct_url(p)
                for role, tok in toks.items():
                    code = call(cli, base, m, rp, tok, body)
                    denied = code in (401, 403)
                    expect_allow = need in ALLOWED[role]
                    if code < 0 or code >= 500:
                        out["violations"].append(f"{path_kind} {role} {m} {p} -> {code} (error)")
                        bad += 1
                    elif expect_allow == (not denied):
                        ok += 1
                    else:
                        bad += 1
                        out["violations"].append(
                            f"{path_kind} {role:10s} {m:6s} {p} -> {code} "
                            f"(expected {'allow' if expect_allow else 'deny'})")
            out["role_matrix"][path_kind] = {"checks": ok + bad, "conform": ok, "violations": bad}

        # 2. forged / invalid tokens, every route, edge + direct
        for name, tok in forged_variants(toks["player"]).items():
            res = {}
            for path_kind in ("edge", "direct"):
                rej = acc = 0
                for (m, p, _need, body) in ROUTES:
                    base, rp = (EDGE, p) if path_kind == "edge" else direct_url(p)
                    code = call(cli, base, m, rp, tok, body)
                    if code == 401:
                        rej += 1
                    else:
                        acc += 1
                        out["violations"].append(f"forged[{name}] {path_kind} {m} {p} -> {code}")
                res[path_kind] = {"rejected_401": rej, "not_rejected": acc}
            out["forged"][name] = res

        # 3. what a compromised non-IAM service could try
        # It holds only the public verification key and its own event key;
        # neither can produce a token any service accepts (RS256, SEC-008).
        res = {}
        for name in ("HS256 w/ public key", "service event key"):
            tok = forged_variants(toks["player"])[name]
            res[name] = call(cli, EDGE, "GET", "/iam/settings/registration-secret", tok, False)
        out["compromised_service_forgery"] = {"route": "GET /iam/settings/registration-secret",
                                              "status": res, "accepted": any(c == 200 for c in res.values())}
        # 4. control-plane-only paths are never proxied
        out["internal_paths"] = {who: call(cli, EDGE, "GET", "/iam/internal/sessions/state", t, False)
                                 for who, t in (("anonymous", None), ("admin", toks["admin"]))}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
