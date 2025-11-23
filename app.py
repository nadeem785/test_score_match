# app.py (Multi-League Soccer + CricAPI currentMatches cricket integration)
import time
import threading
import requests
import os
from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room

# Flask + SocketIO setup
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'secret-key')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

POLL_INTERVAL = 10  # seconds
matches = {}        # cached data per poll_key
poll_threads = {}
poll_lock = threading.Lock()

# ---------------- Soccer ----------------
SOCCER_LEAGUES = {
    "EPL": "eng.1",
    "La Liga": "esp.1",
    "Serie A": "ita.1",
    "Bundesliga": "ger.1",
    "Ligue 1": "fra.1",
    "UCL": "uefa.champions"
}
def soccer_league_url(code):
    return f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard"

def fetch_soccer_data(league_code):
    try:
        headers = {"User-Agent":"Mozilla/5.0","Accept":"application/json"}
        r = requests.get(soccer_league_url(league_code), timeout=10, headers=headers)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("[SOCCER ERROR]", e)
        return None

def map_soccer_state(data):
    if not data or "events" not in data:
        return {"matches": [], "updated": time.time()}
    out = []
    for ev in data.get("events", []):
        try:
            comp = ev.get("competitions", [])[0]
            comps = comp.get("competitors", [])
            home = next((c for c in comps if c.get("homeAway")=="home"), comps[0] if comps else {})
            away = next((c for c in comps if c.get("homeAway")=="away"), comps[1] if len(comps)>1 else {})
            out.append({
                "home_team": home.get("team", {}).get("displayName","Home"),
                "away_team": away.get("team", {}).get("displayName","Away"),
                "home_score": int(home.get("score") or 0),
                "away_score": int(away.get("score") or 0),
                "status": comp.get("status", {}).get("type", {}).get("description",""),
                "time": comp.get("status", {}).get("type", {}).get("shortDetail","")
            })
        except Exception:
            continue
    return {"matches": out, "updated": time.time()}

def soccer_poll_loop(room_id, league_code):
    """Poll soccer scoreboard for given league and emit to room_id"""
    poll_key = f"soccer:{room_id}:{league_code}"
    print(f"[POLL] Soccer {room_id} -> {league_code}")
    while True:
        try:
            raw = fetch_soccer_data(league_code)
            mapped = map_soccer_state(raw)
            if mapped:
                matches[poll_key] = mapped
                socketio.emit("league:update", {
                    "id": room_id,
                    "matches": mapped["matches"],
                    "last_updated": mapped["updated"]
                }, room=room_id)
        except Exception as e:
            print("[soccer_poll_loop] error ->", e)
        time.sleep(POLL_INTERVAL)

# ---------------- Cricket (CricAPI currentMatches mapper) ----------------
CRICAPI_KEY = os.environ.get('CRICAPI_KEY', '69be3aaf-e3a4-4d69-8298-c443223afb36')
CRICAPI_CURRENT = f"https://api.cricapi.com/v1/currentMatches?apikey={CRICAPI_KEY}"

def fetch_cricket_current():
    try:
        headers = {"User-Agent":"Mozilla/5.0","Accept":"application/json"}
        r = requests.get(CRICAPI_CURRENT, timeout=12, headers=headers)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("[CRICKET] fetch error ->", e)
        return None

def map_cricket_state_from_current(json_data):
    if not json_data:
        return {"matches": [], "cards": [], "updated": time.time()}

    arr = json_data.get("data") or json_data.get("matches") or json_data.get("result") or []
    if not isinstance(arr, list):
        arr = list(arr) if isinstance(arr, dict) else []

    summary = []
    cards = []

    for ev in arr:
        try:
            mid = ev.get("id")
            name = ev.get("name", "")
            matchType = ev.get("matchType", "")
            status = ev.get("status", "")
            venue = ev.get("venue", "")
            teams = ev.get("teams", [])  # [home, away]
            teamInfo = ev.get("teamInfo", [])  # objects with name, shortname, img
            score_list = ev.get("score", [])  # list of innings objects with r,w,o,inning

            def find_latest_for_team(team_name):
                for s in reversed(score_list):
                    if not s:
                        continue
                    inn = (s.get("inning") or "").lower()
                    if team_name and team_name.lower() in inn:
                        return s
                return score_list[-1] if score_list else {}

            home = teams[0] if len(teams) > 0 else (teamInfo[0].get("name") if teamInfo else "Home")
            away = teams[1] if len(teams) > 1 else (teamInfo[1].get("name") if len(teamInfo)>1 else "Away")

            home_score_obj = find_latest_for_team(home)
            away_score_obj = find_latest_for_team(away)

            def score_str(obj):
                if not obj:
                    return "0/0 (0.0)"
                r = obj.get("r", obj.get("runs") or 0)
                w = obj.get("w", obj.get("wickets") or 0)
                o = obj.get("o", obj.get("overs") or 0)
                o_str = str(o)
                return f"{r}/{w} ({o_str})"

            summary.append({
                "id": mid,
                "home_team": home,
                "away_team": away,
                "home_score": score_str(home_score_obj),
                "away_score": score_str(away_score_obj),
                "status": status,
                "time": ev.get("dateTimeGMT") or ev.get("date") or ""
            })

            teams_card = []
            for i, tname in enumerate([home, away]):
                tinfo = teamInfo[i] if i < len(teamInfo) else {}
                s_obj = find_latest_for_team(tname)
                runs = s_obj.get("r") or s_obj.get("runs") or 0
                wickets = s_obj.get("w") or s_obj.get("wickets") or 0
                overs = s_obj.get("o") or s_obj.get("overs") or ""
                teams_card.append({
                    "team": tname,
                    "shortname": tinfo.get("shortname") or tname[:3].upper(),
                    "img": tinfo.get("img") or "",
                    "runs": runs,
                    "wickets": wickets,
                    "overs": overs
                })

            cards.append({
                "id": mid,
                "name": name,
                "matchType": matchType,
                "status": status,
                "venue": venue,
                "teams": teams_card,
                "raw_score_list": score_list
            })
        except Exception as e:
            print("[map_cricket_state] skipped event ->", e)
            continue

    return {"matches": summary, "cards": cards, "updated": time.time()}

def cricket_poll_loop(room_id="cricket"):
    print("[POLL] Cricket poller started (using cricapi currentMatches)")
    poll_key = "cricket"
    while True:
        try:
            raw = fetch_cricket_current()
            mapped = map_cricket_state_from_current(raw)
            if mapped:
                matches[poll_key] = mapped
                socketio.emit("cricket:update", {
                    "id": "cricket",
                    "matches": mapped.get("matches", []),
                    "cards": mapped.get("cards", []),
                    "last_updated": mapped.get("updated")
                }, room=room_id)
        except Exception as e:
            print("[cricket_poll_loop] error ->", e)
        time.sleep(POLL_INTERVAL)

# ---------------- Socket events ----------------
@socketio.on("cricket:subscribe")
def on_cricket_subscribe(data):
    room_id = "cricket"
    join_room(room_id)
    print("[WS] subscribed to cricket room")
    with poll_lock:
        if "cricket" not in poll_threads:
            t = threading.Thread(target=cricket_poll_loop, args=(room_id,), daemon=True)
            poll_threads["cricket"] = t
            t.start()
    cached = matches.get("cricket")
    if cached:
        socketio.emit("cricket:update", {"id":"cricket","matches":cached.get("matches",[]),"cards":cached.get("cards",[]),"last_updated":cached.get("updated")}, room=room_id)
    else:
        socketio.emit("cricket:update", {"id":"cricket","matches":[],"info":"Fetching initial cricket data..."}, room=room_id)

@socketio.on("cricket:unsubscribe")
def on_cricket_unsub(data):
    leave_room("cricket")
    print("[WS] unsubscribed from cricket")

@socketio.on("league:subscribe")
def on_league_subscribe(data):
    league_name = data.get("league","EPL")
    room_id = league_name
    league_code = SOCCER_LEAGUES.get(league_name, "eng.1")
    join_room(room_id)
    print("[WS] subscribe league:", league_name)
    poll_key = f"soccer:{room_id}:{league_code}"
    with poll_lock:
        if poll_key not in poll_threads:
            t = threading.Thread(target=soccer_poll_loop, args=(room_id, league_code), daemon=True)
            poll_threads[poll_key] = t
            t.start()
    # send cached data if available
    cached = matches.get(poll_key)
    if cached:
        socketio.emit("league:update", {"id": room_id, "matches": cached["matches"], "last_updated": cached["updated"]}, room=room_id)
    else:
        socketio.emit("league:update", {"id": room_id, "matches": [], "info": "Fetching initial data..."}, room=room_id)

@socketio.on("league:unsubscribe")
def on_league_unsub(data):
    league_name = data.get("league")
    leave_room(league_name)
    print("[WS] unsubscribed from league:", league_name)

# Basic route
@app.route("/")
def home():
    return render_template("index.html", leagues=list(SOCCER_LEAGUES.keys()))

@app.route("/api/test/cricket")
def test_cricket():
    data = fetch_cricket_current()
    ok = bool(data and (data.get("data") or data.get("matches")))
    return jsonify({"ok": ok, "sample_keys": list(data.keys()) if isinstance(data, dict) else []})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("Starting server on port", port)
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
