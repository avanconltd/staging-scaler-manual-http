import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor

import azure.functions as func

from shared_code import audit
from shared_code.de_scaler import scale_all
from shared_code.web_runner import run_web_down, run_web_up

TRUTHY = {"true", "1", "yes"}


def _param(req, req_body, name):
    value = req.params.get(name)
    if value is None:
        value = req_body.get(name)
    return value


def _is_true(value):
    return str(value).strip().lower() in TRUTHY if value is not None else False


def _json_response(body, status_code=200, headers=None):
    return func.HttpResponse(
        json.dumps(body, default=str), status_code=status_code, mimetype="application/json", headers=headers
    )


def _client_ip(req):
    # Azure front ends put the real caller first in X-Forwarded-For ("ip:port, proxy, ...").
    forwarded = req.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    if forwarded.count(":") == 1:
        forwarded = forwarded.split(":")[0]
    return forwarded or None


def _scale(scale_direction, run_de, run_web):
    """Returns (response_body, status_code)."""
    jobs = {}
    if run_de:
        jobs["de"] = lambda: scale_all(scale_direction)
    if run_web:
        jobs["web"] = run_web_up if scale_direction == "up" else run_web_down

    response = {"scale": scale_direction, "de": "skipped", "web": "skipped"}
    failed = []

    # DE and web touch independent resources, so run them side by side.
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {name: pool.submit(job) for name, job in jobs.items()}
        for name, future in futures.items():
            try:
                response[name] = future.result()
            except Exception as e:
                logging.exception("ScaleEnvironment %s scale %s FAILED", name, scale_direction)
                response[name] = {"error": str(e)}
                failed.append(name)

    if failed:
        return response, 500 if len(failed) == len(jobs) else 207
    return response, 200


def main(req: func.HttpRequest) -> func.HttpResponse:
    started = time.monotonic()
    try:
        req_body = req.get_json()
        if not isinstance(req_body, dict):
            req_body = {}
    except Exception:
        req_body = {}

    scale_direction = str(_param(req, req_body, "scale") or "").strip().lower()
    run_de = _is_true(_param(req, req_body, "de"))
    run_web = _is_true(_param(req, req_body, "web"))
    username, password = audit.parse_basic_auth(req.headers.get("Authorization"))

    # Every call is recorded before anything else happens; no database means no scaling.
    try:
        auth_ok, audit_id = audit.begin_invocation(
            username, password, _client_ip(req), req.headers.get("User-Agent"), scale_direction, run_de, run_web
        )
    except Exception:
        logging.exception("ScaleEnvironment: login/audit database unavailable")
        return _json_response({"error": "login service unavailable, try again later"}, 503)

    if not auth_ok:
        body, status = {"error": "invalid or missing username/password"}, 401
    elif scale_direction not in ("up", "down"):
        body, status = {"error": "scale parameter is required and must be 'up' or 'down'"}, 400
    elif not run_de and not run_web:
        body, status = {"error": "nothing to scale: pass de=true and/or web=true"}, 400
    else:
        logging.info("ScaleEnvironment: %s requested scale=%s de=%s web=%s", username, scale_direction, run_de, run_web)
        body, status = _scale(scale_direction, run_de, run_web)
        body["invoked_by"] = username
        body["audit_id"] = audit_id

    result_json = json.dumps(body, default=str)
    try:
        audit.finish_invocation(audit_id, status, result_json, int((time.monotonic() - started) * 1000))
    except Exception:
        # The start row already identifies the caller; don't fail a finished scale over this.
        logging.exception("ScaleEnvironment: could not record result for audit id %s", audit_id)

    headers = {"WWW-Authenticate": 'Basic realm="staging-env-scaler", charset="UTF-8"'} if status == 401 else None
    return func.HttpResponse(result_json, status_code=status, mimetype="application/json", headers=headers)
