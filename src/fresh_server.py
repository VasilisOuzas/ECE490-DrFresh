from flask import Flask, request, jsonify, render_template, Response
import sqlite3
import time
import json
import threading

import script

app = Flask(__name__, template_folder=".")


def get_db():
    conn = sqlite3.connect("drfresh.db")
    conn.row_factory = sqlite3.Row
    return conn


def read_state():
    conn = get_db()
    try:
        tanks = {
            row["name"]: round(row["volume"], 3)
            for row in conn.execute("SELECT name, volume FROM tanks")
        }

        cutoff = int(time.time()) - 5
        alert_row = conn.execute(
            "SELECT alert FROM alerts WHERE timestamp >= ? ORDER BY id DESC LIMIT 1",
            (cutoff,),
        ).fetchone()

        return {
            "tanks": tanks,
            "alert": alert_row["alert"] if alert_row else None
        }
    finally:
        conn.close()


@app.route("/")
def index():
    return render_template(
        "index.html",
        tank_max=script.TANK_MAX,
        default_amount=script.DEFAULT_AMOUNT
    )


@app.route("/api/stream")
def stream():
    def generate():
        last = {}
        while True:
            state = read_state()
            if state != last:
                yield f"data: {json.dumps(state)}\n\n"
                last = state
            else:
                yield ": heartbeat\n\n"
            time.sleep(2)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no"
        }
    )


@app.route("/api/dispense", methods=["POST"])
def dispense():
    command = request.get_json(force=True)

    if not command or "tank" not in command or "type" not in command:
        return jsonify({"status": "error", "message": "Missing tank or type"}), 400

    if command["tank"] not in ("A", "B"):
        return jsonify({"status": "error", "message": "Invalid tank"}), 400

    if command["type"] not in ("auto", "manual"):
        return jsonify({"status": "error", "message": "Invalid type"}), 400

    try:
        script.handle_command(command)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/refill", methods=["POST"])
def refill():
    payload = request.get_json(force=True)

    if not payload or "tank" not in payload:
        return jsonify({"status": "error", "message": "Missing tank"}), 400

    if payload["tank"] not in ("A", "B"):
        return jsonify({"status": "error", "message": "Invalid tank"}), 400

    if "volume" not in payload:
        payload["volume"] = script.TANK_MAX

    try:
        script.handle_refill(payload)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    threading.Thread(target=script.main, daemon=True).start()
    time.sleep(1)
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
