"""Bearer-token authentication for the ingestion service.

We are behind Nginx-Proxy-Manager (NOT Cloudflare Access), so the backend owns
auth. The phone sends `Authorization: Bearer <token>`; we compare against
HEALTHBRIDGE_TOKEN using a constant-time comparison.

Local dev: set HEALTHBRIDGE_DEV=1 to bypass (only do this on localhost).

NOTE for Claude Code: implement verify_bearer. Use hmac.compare_digest for the
comparison. Raise HTTPException(401) on missing/empty header, 403 on mismatch.
"""

from __future__ import annotations

import hmac
import os

from fastapi import HTTPException

EXPECTED_TOKEN = os.environ.get("HEALTHBRIDGE_TOKEN", "")
DEV_MODE = os.environ.get("HEALTHBRIDGE_DEV", "") == "1"


def verify_bearer(authorization: str | None) -> None:
    """Validate an `Authorization: Bearer <token>` header.

    - DEV_MODE: skip entirely (localhost only).
    - Missing/!startswith 'Bearer ': 401.
    - Token mismatch (constant-time): 403.
    - EXPECTED_TOKEN empty in non-dev: refuse to run (misconfiguration) -> 500.

    TODO(claude-code): implement exactly this contract; add a unit test for each
    branch (dev bypass, missing header, wrong token, correct token, unconfigured).
    """
    raise NotImplementedError
