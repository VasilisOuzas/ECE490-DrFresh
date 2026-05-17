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
DEFAULT_AMOUNT  = 0.3
WATCHDOG_WINDOW = 0.75
client          = None
manual_state    = {}
auto_state      = {}

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
        if cursor.execute("SELECT COUNT(*) FROM tanks").fetchone()[0] == 0:
            cursor.execute("INSERT INTO tanks (name, volume) VALUES ('A', ?)", (TANK_MAX,))
            cursor.execute("INSERT INTO tanks (name, volume) VALUES ('B', ?)", (TANK_MAX,))
        conn.commit()
    except Exception as e:
        print(f"Error creating database: {e}")
    finally:
        if conn:
            conn.close()


def safe_publish(topic, payload):
    if client is not None:
        client.publish(topic, json.dumps(payload))
    else:
        print(f"MQTT client not ready, could not publish to {topic}")


def close_auto(tank):
    conn = None
    try:
        auto_state.pop(tank)
        pump_pin = PUMP_A_PIN if tank == "A" else PUMP_B_PIN
        GPIO.output(pump_pin, GPIO.HIGH)
        conn = sqlite3.connect('drfresh.db')
        cursor = conn.cursor()
        cursor.execute("SELECT volume FROM tanks WHERE name=?", (tank,))
        current_volume = cursor.fetchone()[0]
        new_volume = max(round(current_volume - DEFAULT_AMOUNT, 3), 0.0)
        cursor.execute("UPDATE tanks SET volume=? WHERE name=?", (new_volume, tank))
        conn.commit()
        safe_publish("drfresh/status", {"tank": tank, "type": "auto", "amount": DEFAULT_AMOUNT, "status": "success"})
        safe_publish("drfresh/volume", {"tank": tank, "volume": new_volume})
        print(f"Auto dispense complete for tank '{tank}'. Dispensed {DEFAULT_AMOUNT}L. New volume: {new_volume}L")
        if new_volume < TANK_MIN:
            safe_publish("drfresh/alert", {"tank": tank, "alert": "Tank is low. Please refill soon."})
            print(f"Tank '{tank}' is low. Please refill soon.")
    except Exception as e:
        print(f"Error closing auto dispense: {e}")
    finally:
        if conn:
            conn.close()

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
        new_volume = max(round(current_volume - amount, 3), 0.0)
        cursor.execute("UPDATE tanks SET volume=? WHERE name=?", (new_volume, tank))
        conn.commit()
        safe_publish("drfresh/status", {"tank": tank, "type": "manual", "amount": amount, "status": "success"})
        safe_publish("drfresh/volume", {"tank": tank, "volume": new_volume})
        print(f"Manual dispense closed for tank '{tank}'. Dispensed {amount}L. New volume: {new_volume}L")
        if new_volume < TANK_MIN:
            safe_publish("drfresh/alert", {"tank": tank, "alert": "Tank is low. Please refill soon."})
            print(f"Tank '{tank}' is low. Please refill soon.")
    except Exception as e:
        print(f"Error closing manual dispense: {e}")
    finally:
        if conn:
            conn.close()


def check_watchdog():
    for tank in list(auto_state.keys()):
        state = auto_state[tank]
        if time.time() - state["start_time"] >= state["timeout"]:
            print(f"Auto dispense complete for tank '{tank}'")
            close_auto(tank)

    for tank in list(manual_state.keys()):
        state = manual_state[tank]
        if time.time() - state["start_time"] >= state["timeout"]:
            print(f"Tank '{tank}' ran empty — closing pump")
            safe_publish("drfresh/alert", {"tank": tank, "alert": "Tank ran empty during manual dispense."})
            close_manual(tank)
            continue
        if time.time() - state["last_message"] > WATCHDOG_WINDOW:
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
            safe_publish("drfresh/alert", {"tank": tank, "alert": "Tank is empty. Cannot dispense."})
            safe_publish("drfresh/status", {"tank": tank, "type": dispense_type, "amount": 0, "status": "failed"})
            return

        if dispense_type == "auto":

            if tank in auto_state or tank in manual_state:
                print(f"Tank '{tank}' is already dispensing. Ignoring auto command.")
                safe_publish("drfresh/status", {"tank": tank, "type": "auto", "amount": 0, "status": "failed"})
                safe_publish("drfresh/alert", {"tank": tank, "alert": "Tank is already dispensing. Cannot start auto dispense."})
                return
            if current_volume < DEFAULT_AMOUNT:
                print(f"Tank '{tank}' is too low to dispense.")
                safe_publish("drfresh/alert", {"tank": tank, "alert": f"Tank is too low to dispense {DEFAULT_AMOUNT}L"})
                safe_publish("drfresh/status", {"tank": tank, "type": "auto", "amount": 0, "status": "failed"})
                return
            pump_pin = PUMP_A_PIN if tank == "A" else PUMP_B_PIN
            GPIO.output(pump_pin, GPIO.LOW)
            auto_state[tank] = {
                "start_time": time.time(),
                "timeout":    DEFAULT_AMOUNT / FLOW_RATE,
            }
            print(f"Auto dispense started for tank '{tank}'")

        elif dispense_type == "manual":
            if tank in manual_state:
                manual_state[tank]["last_message"] = time.time()
            else:
                if tank in auto_state:
                    print(f"Tank '{tank}' is already dispensing. Ignoring manual command.")
                    safe_publish("drfresh/status", {"tank": tank, "type": "manual", "amount": 0, "status": "failed"})
                    safe_publish("drfresh/alert", {"tank": tank, "alert": "Tank is already dispensing. Cannot start manual dispense."})
                    return
                pump_pin = PUMP_A_PIN if tank == "A" else PUMP_B_PIN
                GPIO.output(pump_pin, GPIO.LOW)
                manual_state[tank] = {
                    "start_time":   time.time(),
                    "last_message": time.time(),
                    "timeout":      current_volume / FLOW_RATE
                }
                print(f"Manual dispense started for tank '{tank}', "
                      f"will run empty in {round(current_volume / FLOW_RATE, 1)}s")

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
        safe_publish("drfresh/status", {"tank": tank, "type": "refill", "amount": volume, "status": "success"})
        safe_publish("drfresh/volume", {"tank": tank, "volume": round(new_volume, 3)})
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
        time.sleep(0.1)
        conn = None
        try:
            conn = sqlite3.connect('drfresh.db')
            cursor = conn.cursor()
            cursor.execute("SELECT name, volume FROM tanks")
            for name, volume in cursor.fetchall():
                client.publish("drfresh/volume", json.dumps({"tank": name, "volume": volume}))
                print(f"Startup: Tank {name} = {volume}L")
        except Exception as e:
            print(f"Error publishing startup volumes: {e}")
        finally:
            if conn:
                conn.close()
        while True:
            check_watchdog()
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        for tank in list(auto_state.keys()):
            close_auto(tank)
        for tank in list(manual_state.keys()):
            close_manual(tank)
        if client is not None:
            client.loop_stop()
        GPIO.cleanup()

if __name__ == "__main__":
    main()
