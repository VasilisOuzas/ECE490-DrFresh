import paho.mqtt.client as mqtt
import json
import time
import RPi.GPIO as GPIO
import sqlite3
import uuid

BROKER          = "localhost"
PUMP_A_PIN      = 17
PUMP_B_PIN      = 27
TANK_MIN        = 0.1
TANK_MAX        = 1.5
FLOW_RATE       = 0.03
MANUAL_TIMEOUT  = 10
DEFAULT_AMOUNT  = 0.3
WATCHDOG_WINDOW = 0.75
client          = None
manual_state    = {}

client_id = f"drfresh_{uuid.uuid4().hex[:8]}"

def create_database():
    conn = None
    try:
        conn = sqlite3.connect('drfresh.db')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tanks (
                name TEXT PRIMARY KEY,
                volume REAL NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tank TEXT NOT NULL,
                type TEXT NOT NULL,
                status TEXT NOT NULL,
                amount REAL,
                timestamp INTEGER NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tank TEXT NOT NULL,
                alert TEXT NOT NULL,
                timestamp INTEGER NOT NULL
            )
        ''')
        if cursor.execute("SELECT COUNT(*) FROM tanks").fetchone()[0] == 0:
            cursor.execute("INSERT INTO tanks (name, volume) VALUES ('A', ?)", (TANK_MAX,))
            cursor.execute("INSERT INTO tanks (name, volume) VALUES ('B', ?)", (TANK_MAX,))
        conn.commit()
    except Exception as e:
        print(f"Error creating database: {e}")
    finally:
        if conn:
            conn.close()

def store_command(command, status, amount):
    conn = None
    try:
        conn = sqlite3.connect('drfresh.db')
        cursor = conn.cursor()
        cursor.execute("INSERT INTO commands (tank, type, status, amount, timestamp) VALUES (?, ?, ?, ?, ?)",
                       (command.get("tank"), command.get("type"), status, amount, int(time.time())))
        conn.commit()
    except Exception as e:
        print(f"Error storing command: {e}")
    finally:
        if conn:
            conn.close()

def store_alert(tank, alert):
    conn = None
    try:
        conn = sqlite3.connect('drfresh.db')
        cursor = conn.cursor()
        cursor.execute("INSERT INTO alerts (tank, alert, timestamp) VALUES (?, ?, ?)",
                       (tank, alert, int(time.time())))
        conn.commit()
    except Exception as e:
        print(f"Error storing alert: {e}")
    finally:
        if conn:
            conn.close()

def safe_publish(topic, payload):
    if client is not None:
        client.publish(topic, json.dumps(payload))
    else:
        print(f"MQTT client not ready, could not publish to {topic}")

def auto_dispense(tank):
    pump_pin = PUMP_A_PIN if tank == "A" else PUMP_B_PIN
    dispense_time = DEFAULT_AMOUNT / FLOW_RATE
    try:
        GPIO.output(pump_pin, GPIO.LOW)
        print(f"Auto dispensing {DEFAULT_AMOUNT}L from tank {tank}")
        time.sleep(dispense_time)
    finally:
        GPIO.output(pump_pin, GPIO.HIGH)
        print(f"Finished auto dispensing from tank {tank}")

def close_manual(tank):
    conn = None
    try:
        state = manual_state.pop(tank)
        pump_pin = PUMP_A_PIN if tank == "A" else PUMP_B_PIN
        GPIO.output(pump_pin, GPIO.HIGH)
        elapsed = time.time() - state["start_time"]
        amount = round(elapsed * FLOW_RATE, 3)
        conn = sqlite3.connect('drfresh.db')
        cursor = conn.cursor()
        cursor.execute("SELECT volume FROM tanks WHERE name=?", (tank,))
        current_volume = cursor.fetchone()[0]
        new_volume = round(current_volume - amount, 3)
        cursor.execute("UPDATE tanks SET volume=? WHERE name=?", (new_volume, tank))
        conn.commit()
        store_command({"tank": tank, "type": "manual"}, "success", amount)
        publish_command = {"tank": tank, "type": "manual", "amount": amount, "status": "success", "volume": new_volume}
        safe_publish("drfresh/status", publish_command)
        print(f"Manual dispense closed for tank '{tank}'. Dispensed {amount}L. New volume: {new_volume}L")
        if new_volume < TANK_MIN:
            publish_alert = {"tank": tank, "alert": "Tank is low. Please refill soon."}
            safe_publish("drfresh/alert", publish_alert)
            store_alert(tank, "Tank is low. Please refill soon.")
    except Exception as e:
        print(f"Error closing manual dispense: {e}")
    finally:
        if conn:
            conn.close()

def check_manual_watchdog():
    for tank in list(manual_state.keys()):
        last_message = manual_state[tank]["last_message"]
        if time.time() - last_message > WATCHDOG_WINDOW:
            print(f"Watchdog closing tank '{tank}' — no message in {WATCHDOG_WINDOW}s")
            close_manual(tank)

def handle_command(command):
    conn = None
    try:
        tank = command.get("tank")
        dispense_type = command.get("type")

        conn = sqlite3.connect('drfresh.db')
        cursor = conn.cursor()
        cursor.execute("SELECT volume FROM tanks WHERE name=?", (tank,))
        result = cursor.fetchone()
        if not result:
            print(f"Tank '{tank}' not found in database.")
            return
        current_volume = result[0]

        if current_volume < 0.001:
            print(f"Tank '{tank}' is empty. Cannot dispense.")
            publish_alert = {"tank": tank, "alert": "Tank is empty. Cannot dispense."}
            safe_publish("drfresh/alert", publish_alert)
            publish_command = {"tank": tank, "type": dispense_type, "amount": 0, "status": "failed"}
            safe_publish("drfresh/status", publish_command)
            store_command(command, "failed", 0)
            store_alert(tank, "Tank is empty. Cannot dispense.")
            return

        if dispense_type == "auto":
            if current_volume < DEFAULT_AMOUNT:
                print(f"Tank '{tank}' is too low to dispense.")
                publish_alert = {"tank": tank, "alert": f"Tank is too low to dispense {DEFAULT_AMOUNT}L"}
                safe_publish("drfresh/alert", publish_alert)
                store_command(command, "failed", 0)
                publish_command = {"tank": tank, "type": "auto", "amount": 0, "status": "failed"}
                safe_publish("drfresh/status", publish_command)
                store_alert(tank, f"Tank is too low to dispense {DEFAULT_AMOUNT}L")
                return
            else:
                print(f"Auto-dispensing {DEFAULT_AMOUNT}L from tank '{tank}'")
                conn.close()
                conn = None
                auto_dispense(tank)
                new_volume = round(current_volume - DEFAULT_AMOUNT, 3)
                conn = sqlite3.connect('drfresh.db')
                cursor = conn.cursor()
                cursor.execute("UPDATE tanks SET volume=? WHERE name=?", (new_volume, tank))
                conn.commit()
                store_command(command, "success", DEFAULT_AMOUNT)
                print(f"Dispensed from '{tank}'. New volume: {new_volume}L")
                publish_command = {"tank": tank, "type": "auto", "amount": DEFAULT_AMOUNT, "status": "success", "volume": new_volume}
                safe_publish("drfresh/status", publish_command)
                if new_volume < TANK_MIN:
                    print(f"Tank '{tank}' is low. Please refill soon.")
                    publish_alert = {"tank": tank, "alert": "Tank is low. Please refill soon."}
                    safe_publish("drfresh/alert", publish_alert)
                    store_alert(tank, "Tank is low. Please refill soon.")

        elif dispense_type == "manual":
            if tank in manual_state:
                elapsed = time.time() - manual_state[tank]["start_time"]
                if current_volume <= 0.001 or elapsed >= manual_state[tank]["timeout"]:
                    close_manual(tank)
                else:
                    manual_state[tank]["last_message"] = time.time()
            else:
                max_dispensable = MANUAL_TIMEOUT * FLOW_RATE
                timeout = MANUAL_TIMEOUT if current_volume >= max_dispensable else current_volume / FLOW_RATE
                pump_pin = PUMP_A_PIN if tank == "A" else PUMP_B_PIN
                GPIO.output(pump_pin, GPIO.LOW)
                manual_state[tank] = {
                    "start_time": time.time(),
                    "last_message": time.time(),
                    "timeout": timeout
                }
                print(f"Manual dispense started for tank '{tank}', max timeout {round(timeout, 1)}s")

    except Exception as e:
        print(f"Error handling command: {e}")
    finally:
        if conn:
            conn.close()

def handle_refill(refill):
    conn = None
    try:
        tank = refill.get("tank")
        volume = refill.get("volume")

        conn = sqlite3.connect('drfresh.db')
        cursor = conn.cursor()
        cursor.execute("SELECT volume FROM tanks WHERE name=?", (tank,))
        result = cursor.fetchone()
        if not result:
            print(f"Tank '{tank}' not found in database.")
            return
        current_volume = result[0]

        new_volume = min(current_volume + volume, TANK_MAX)
        cursor.execute("UPDATE tanks SET volume=? WHERE name=?", (new_volume, tank))
        conn.commit()
        print(f"Refilled '{tank}' by {volume}L. New volume: {new_volume}L")
        publish_command = {"tank": tank, "type": "refill", "amount": volume, "status": "success", "volume": round(new_volume, 3)}
        safe_publish("drfresh/status", publish_command)
    except Exception as e:
        print(f"Error handling refill: {e}")
    finally:
        if conn:
            conn.close()


def on_connect(client, userdata, flags, reason_code, properties):
    print(f"Connected with result code {reason_code}")
    client.subscribe("drfresh/command")
    client.subscribe("drfresh/refill")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        if msg.topic == "drfresh/command":
            handle_command(payload)
        elif msg.topic == "drfresh/refill":
            handle_refill(payload)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON on {msg.topic}: {e}")
    except Exception as e:
        print(f"Error processing message on {msg.topic}: {e}")

def connect_mqtt_client() -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id)
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect(BROKER, 1883)
    return client

def main():
    global client
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(PUMP_A_PIN, GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(PUMP_B_PIN, GPIO.OUT, initial=GPIO.HIGH)
    create_database()
    try:
        client = connect_mqtt_client()
        client.loop_start()
        while True:
            check_manual_watchdog()
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        for tank in list(manual_state.keys()):
            close_manual(tank)
        if client is not None:
            client.loop_stop()
        GPIO.cleanup()

if __name__ == "__main__":
    main()
