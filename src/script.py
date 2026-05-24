import paho.mqtt.client as mqtt
import json
import time
import RPi.GPIO as GPIO
import sqlite3
import uuid
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

BROKER = "194.177.207.38"
PORT = 1883
MQTT_USERNAME = "team1"
MQTT_PASSWORD = "team1!@#$"

TEAM = "team1"
ALERT_TOPIC = f"iot/{TEAM}/drfresh/alert"
REFILL_TOPIC = f"iot/{TEAM}/drfresh/refill"

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

INFLUX_URL    = "http://localhost:8086"
INFLUX_TOKEN  = "n8XW48pHdCUZ3Ox7Pmzi9ACKBPyKsaxJemdGElebSoYqMZBQgfHW6cpMWHTYrB8J3H4_AvtISdbletjXeZbGUQ=="
INFLUX_ORG    = "drfresh"
INFLUX_BUCKET = "drfresh"

client_id = f"drfresh_{uuid.uuid4().hex[:8]}"

influx_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
write_api     = influx_client.write_api(write_options=SYNCHRONOUS)
query_api     = influx_client.query_api()

def influx_write_event(tank, dispense_type, amount, status):
    """Write a dispense or refill event to InfluxDB."""
    try:
        point = (
            Point("dispense_event")
            .tag("tank", tank)
            .tag("type", dispense_type)
            .tag("status", status)
            .field("amount", float(amount))
        )
        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
    except Exception as e:
        print(f"InfluxDB write_event error: {e}")

def influx_write_volume(tank, volume):
    """Write a tank volume snapshot to InfluxDB for historical tracking."""
    try:
        point = (
            Point("tank_volume")
            .tag("tank", tank)
            .field("volume", float(volume))
        )
        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
    except Exception as e:
        print(f"InfluxDB write_volume error: {e}")

def influx_write_alert(tank, alert):
    """Write an alert to InfluxDB."""
    try:
        point = (
            Point("alert")
            .tag("tank", tank)
            .field("message", alert)
        )
        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
    except Exception as e:
        print(f"InfluxDB write_alert error: {e}")

def influx_update_analytics(tank):
    """
    Query InfluxDB for all dispense events and alerts for this tank,
    compute analytics, and write them back as a single analytics point.
    Analytics computed:
      - total_volume_dispensed   : total litres dispensed (auto + manual successes)
      - dispense_count           : total number of successful dispenses
      - avg_volume_per_dispense  : average litres per successful dispense
      - auto_count               : number of successful auto dispenses
      - manual_count             : number of successful manual dispenses
      - auto_manual_ratio        : fraction of dispenses that were auto (0.0 - 1.0)
      - refill_count             : number of refill operations
      - low_tank_count           : number of times tank dropped below TANK_MIN
      - empty_tank_count         : number of dispense attempts rejected due to empty/too-low tank
      - tank_ran_empty_count     : number of times tank ran empty during a manual dispense
    """
    try:
        q_total = f'''
        from(bucket: "{INFLUX_BUCKET}")
          |> range(start: 0)
          |> filter(fn: (r) => r._measurement == "dispense_event"
                            and r.tank == "{tank}"
                            and r.status == "success"
                            and (r.type == "auto" or r.type == "manual")
                            and r._field == "amount")
          |> sum()
        '''
        res_total = query_api.query(q_total, org=INFLUX_ORG)
        total_volume = 0.0
        for table in res_total:
            for record in table.records:
                total_volume += record.get_value() or 0.0

        q_counts = f'''
        from(bucket: "{INFLUX_BUCKET}")
          |> range(start: 0)
          |> filter(fn: (r) => r._measurement == "dispense_event"
                            and r.tank == "{tank}"
                            and r.status == "success"
                            and r._field == "amount")
          |> group(columns: ["type"])
          |> count()
        '''
        res_counts = query_api.query(q_counts, org=INFLUX_ORG)
        counts = {"auto": 0, "manual": 0, "refill": 0}
        for table in res_counts:
            for record in table.records:
                t = record.values.get("type")
                if t in counts:
                    counts[t] = record.get_value() or 0

        q_low = f'''
        from(bucket: "{INFLUX_BUCKET}")
          |> range(start: 0)
          |> filter(fn: (r) => r._measurement == "alert"
                            and r.tank == "{tank}"
                            and r._field == "message")
          |> filter(fn: (r) => r._value =~ /low/)
          |> count()
        '''
        res_low = query_api.query(q_low, org=INFLUX_ORG)
        low_count = 0
        for table in res_low:
            for record in table.records:
                low_count += record.get_value() or 0

        q_empty = f'''
        from(bucket: "{INFLUX_BUCKET}")
          |> range(start: 0)
          |> filter(fn: (r) => r._measurement == "alert"
                            and r.tank == "{tank}"
                            and r._field == "message")
          |> filter(fn: (r) => r._value =~ /empty/ or r._value =~ /too low/)
          |> count()
        '''
        res_empty = query_api.query(q_empty, org=INFLUX_ORG)
        empty_count = 0
        for table in res_empty:
            for record in table.records:
                empty_count += record.get_value() or 0

        q_ran_empty = f'''
        from(bucket: "{INFLUX_BUCKET}")
          |> range(start: 0)
          |> filter(fn: (r) => r._measurement == "alert"
                            and r.tank == "{tank}"
                            and r._field == "message")
          |> filter(fn: (r) => r._value =~ /ran empty/)
          |> count()
        '''
        res_ran_empty = query_api.query(q_ran_empty, org=INFLUX_ORG)
        ran_empty_count = 0
        for table in res_ran_empty:
            for record in table.records:
                ran_empty_count += record.get_value() or 0

        dispense_count = counts["auto"] + counts["manual"]
        avg_volume     = round(total_volume / dispense_count, 4) if dispense_count > 0 else 0.0
        ratio          = round(counts["auto"] / dispense_count, 4) if dispense_count > 0 else 0.0

        point = (
            Point("analytics")
            .tag("tank", tank)
            .field("total_volume_dispensed",  round(total_volume, 4))
            .field("dispense_count",          dispense_count)
            .field("avg_volume_per_dispense", avg_volume)
            .field("auto_count",              counts["auto"])
            .field("manual_count",            counts["manual"])
            .field("auto_manual_ratio",       ratio)
            .field("refill_count",            counts["refill"])
            .field("low_tank_count",          low_count)
            .field("empty_tank_count",        empty_count)
            .field("tank_ran_empty_count",    ran_empty_count)
        )
        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
    except Exception as e:
        print(f"InfluxDB update_analytics error: {e}")

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

        print(f"Auto dispense complete for tank '{tank}'. Dispensed {DEFAULT_AMOUNT}L. New volume: {new_volume}L")

        influx_write_event(tank, "auto", DEFAULT_AMOUNT, "success")
        influx_write_volume(tank, new_volume)

        #MQTT publishes on topic only when there is an alert or a refill, not on every dispense, to reduce noise. Analytics can be viewed in InfluxDB.
        if new_volume < TANK_MIN:
            safe_publish(ALERT_TOPIC, {
                "tank": tank,
                "alert": "Tank is low. Please refill soon."
            })
            influx_write_alert(tank, "Tank is low. Please refill soon.")
            print(f"Tank '{tank}' is low. Please refill soon.")

        influx_update_analytics(tank)

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

        print(f"Manual dispense closed for tank '{tank}'. Dispensed {amount}L. New volume: {new_volume}L")

        influx_write_event(tank, "manual", amount, "success")
        influx_write_volume(tank, new_volume)

        #MQTT publishes on topic only when there is an alert or a refill, not on every dispense, to reduce noise. Analytics can be viewed in InfluxDB.
        if new_volume < TANK_MIN:
            safe_publish(ALERT_TOPIC, {
                "tank": tank,
                "alert": "Tank is low. Please refill soon."
            })
            influx_write_alert(tank, "Tank is low. Please refill soon.")
            print(f"Tank '{tank}' is low. Please refill soon.")

        influx_update_analytics(tank)

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
            safe_publish(ALERT_TOPIC, {
                "tank": tank,
                "alert": "Tank ran empty during manual dispense."
            })
            influx_write_alert(tank, "Tank ran empty during manual dispense.")
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
            safe_publish(ALERT_TOPIC, {
                "tank": tank,
                "alert": "Tank is empty. Cannot dispense."
            })
            influx_write_event(tank, dispense_type, 0, "failed")
            influx_write_alert(tank, "Tank is empty. Cannot dispense.")
            influx_update_analytics(tank)
            return

        if dispense_type == "auto":
            if tank in auto_state or tank in manual_state:
                print(f"Tank '{tank}' is already dispensing. Ignoring auto command.")
                influx_write_event(tank, "auto", 0, "failed")
                influx_write_alert(tank, "Tank is already dispensing.")
                influx_update_analytics(tank)
                return
            if current_volume < DEFAULT_AMOUNT:
                print(f"Tank '{tank}' is too low to dispense.")
                safe_publish(ALERT_TOPIC, {
                    "tank": tank,
                    "alert": f"Tank is too low to dispense {DEFAULT_AMOUNT}L"
                })
                influx_write_event(tank, "auto", 0, "failed")
                influx_write_alert(tank, f"Tank is too low to dispense {DEFAULT_AMOUNT}L")
                influx_update_analytics(tank)
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
                    influx_write_event(tank, "manual", 0, "failed")
                    influx_write_alert(tank, "Tank is already dispensing.")
                    influx_update_analytics(tank)
                    return
                pump_pin = PUMP_A_PIN if tank == "A" else PUMP_B_PIN
                GPIO.output(pump_pin, GPIO.LOW)
                manual_state[tank] = {
                    "start_time":   time.time(),
                    "last_message": time.time(),
                    "timeout":      current_volume / FLOW_RATE
                }
                print(f"Manual dispense started for tank '{tank}', "f"will run empty in {round(current_volume / FLOW_RATE, 1)}s")

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
        safe_publish(REFILL_TOPIC, {
            "tank": tank,
            "amount": volume,
            "new_volume": round(new_volume, 3),
            "status": "success"
        }) 
        #Publish refill event to MQTT for real-time updates. Analytics can be viewed in InfluxDB.
        
        influx_write_event(tank, "refill", volume, "success")
        influx_write_volume(tank, new_volume)
        influx_update_analytics(tank)
    except Exception as e:
        print(f"Error handling refill: {e}")
    finally:
        if conn:
            conn.close()

def on_connect(client, userdata, flags, reason_code, properties):
    print(f"Connected with result code {reason_code}")
    client.subscribe(f"iot/{TEAM}/drfresh/command")
    client.subscribe(f"iot/{TEAM}/drfresh/refill")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        if msg.topic == f"iot/{TEAM}/drfresh/command":
            handle_command(payload)
        elif msg.topic == f"iot/{TEAM}/drfresh/refill":
            handle_refill(payload)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON on {msg.topic}: {e}")
    except Exception as e:
        print(f"Error processing message on {msg.topic}: {e}")

def connect_mqtt_client() -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id)
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect(BROKER, PORT)
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
                print(f"Startup: Tank {name} = {volume}L")
                influx_write_volume(name, volume)
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
        influx_client.close()
        GPIO.cleanup()

if __name__ == "__main__":
    main()
