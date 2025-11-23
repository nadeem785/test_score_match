# spawn_clients.py (UPDATED FOR MULTI-LEAGUE SOCCER)
import socketio
import time
import threading

SERVER = 'http://localhost:5000'
CLIENTS = 60

# choose league to stress test
league_name = "EPL"

sockets = []

def make_client(i):
    sio = socketio.Client(logger=False, engineio_logger=False, reconnection=False)

    @sio.event
    def connect():
        sio.emit('league:subscribe', {'league': league_name})
        if i < 2:
            print(f"[client {i}] connected + subscribed to {league_name}")

    @sio.on('league:update')
    def on_update(data):
        if i < 2:
            print(f"[client {i}] received update ({len(data.get('matches', []))} matches)")

    @sio.event
    def disconnect():
        pass

    try:
        sio.connect(SERVER, transports=['websocket'])
        sockets.append(sio)
    except Exception as e:
        print("Failed to connect client", i, e)

threads = []
for i in range(CLIENTS):
    t = threading.Thread(target=make_client, args=(i,), daemon=True)
    threads.append(t)
    t.start()
    time.sleep(0.05)

print(f"Spawned ~{CLIENTS} clients. Press Ctrl+C to stop.")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Disconnecting clients...")
    for s in sockets:
        try:
            s.disconnect()
        except:
            pass
