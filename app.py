"""BillBomb web app.

    python app.py            # live mode if AWS credentials are configured, else demo
    python app.py --demo     # always use the simulated demo account
"""

import sys
import threading

import boto3
from flask import Flask, jsonify, render_template, request, send_file

from billbomb import actions, scanner

app = Flask(__name__)
STATE = {"mode": None, "session": None, "account": "", "ready": False, "findings": [], "regions": []}
LOCK = threading.Lock()


def _boot():
    want_demo = "--demo" in sys.argv
    session = None if want_demo else boto3.Session()
    if session is not None and session.get_credentials() is None:
        session = None
    if session is None:
        from billbomb import demo
        STATE.update(mode="demo", account="Simulated student account", session=demo.start(),
                     regions=demo.DEMO_REGIONS)
    else:
        ident = session.client("sts").get_caller_identity()
        STATE.update(mode="live", account=f"AWS account {ident['Account']}", session=session, regions=None)
    STATE["ready"] = True


def _payload(budget, pkr_rate):
    findings = STATE["findings"]
    return {
        "findings": findings,
        "summary": scanner.summarize(findings, budget, pkr_rate),
        "labels": actions.LABELS,
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def status():
    return jsonify(mode=STATE["mode"], account=STATE["account"], ready=STATE["ready"],
                   regions=STATE["regions"])


@app.post("/api/scan")
def scan():
    body = request.get_json(silent=True) or {}
    if not STATE["ready"]:
        return jsonify(error="Still connecting, try again in a few seconds."), 503
    with LOCK:
        result = scanner.scan(STATE["session"], STATE["regions"], demo=STATE["mode"] == "demo")
        STATE["findings"] = result["findings"]
    out = _payload(float(body.get("budget") or 0), float(body.get("pkr_rate") or 280))
    out.update(regions=result["regions"], errors=result["errors"])
    return jsonify(out)


@app.post("/api/summary")
def summary():
    body = request.get_json(silent=True) or {}
    return jsonify(_payload(float(body.get("budget") or 0), float(body.get("pkr_rate") or 280)))


@app.post("/api/defuse")
def defuse():
    body = request.get_json(silent=True) or {}
    ids = set(body.get("ids") or [])
    results = []
    with LOCK:
        remaining = []
        for f in STATE["findings"]:
            if f["id"] in ids:
                ok, message = actions.defuse(STATE["session"], f)
                results.append({"id": f["id"], "ok": ok, "message": message, "saved": f["monthly_cost"] if ok else 0})
                if ok:
                    continue
            remaining.append(f)
        STATE["findings"] = remaining
    out = _payload(float(body.get("budget") or 0), float(body.get("pkr_rate") or 280))
    out["results"] = results
    return jsonify(out)


@app.post("/api/reset")
def reset():
    if STATE["mode"] != "demo":
        return jsonify(error="Reset only exists in demo mode."), 400
    from billbomb import demo
    with LOCK:
        STATE.update(session=demo.start(), findings=[])
    return jsonify(ok=True)


@app.get("/autostop.yaml")
def autostop():
    return send_file("infra/autostop.yaml", mimetype="text/yaml", as_attachment=True,
                     download_name="billbomb-autostop.yaml")


if __name__ == "__main__":
    threading.Thread(target=_boot, daemon=True).start()
    print("BillBomb running at http://localhost:8080")
    app.run(port=8080, debug=False)
