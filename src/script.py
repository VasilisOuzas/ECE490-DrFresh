import paho.mqtt.client as mqtt
import json
import time
import RPi.GPIO as GPIO
import sqlite3

BROKER     = "localhost"
PUMP_A_PIN = 17 
PUMP_B_PIN = 27    
TANK_MIN   = 0.1 
TANK_MAX   = 1.5  
FLOW_RATE  = 0.5
AUTO_PUMP_A = False
AUTO_PUMP_B = False
MANUAL_TIMEOUT = 7
DEFAULT_AMOUNT = 0.3


client_id = "water_tank_controller"
GPIO.setmode(GPIO.BCM)
GPIO.setup(PUMP_A_PIN, GPIO.OUT, initial=GPIO.HIGH)
GPIO.setup(PUMP_B_PIN, GPIO.OUT, initial=GPIO.HIGH)

def create_database():
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
        conn.close()

def store_command(command, status):
    try:
        conn = sqlite3.connect('drfresh.db')
        cursor = conn.cursor()
        cursor.execute("INSERT INTO commands (tank, type, status, amount, timestamp) VALUES (?, ?, ?, ?, ?)",
                       (command.get("tank"), command.get("type"), status, command.get("amount"), int(time.time())))
        conn.commit()
    except Exception as e:
        print(f"Error storing command: {e}")
    finally:
        conn.close()
        
def store_alert(tank, alert):
    try:
        conn = sqlite3.connect('drfresh.db')
        cursor = conn.cursor()
        cursor.execute("INSERT INTO alerts (tank, alert, timestamp) VALUES (?, ?, ?)",
                       (tank, alert, int(time.time())))
        conn.commit()
    except Exception as e:
        print(f"Error storing alert: {e}")
    finally:
        conn.close()


        
def auto_dispense(tank):
    global AUTO_PUMP_A
    global AUTO_PUMP_B
    
    pump_pin = PUMP_A_PIN if tank == "A" else PUMP_B_PIN
    
    dispense_time = DEFAULT_AMOUNT / FLOW_RATE
    
    if tank == "A":
        AUTO_PUMP_A = True
    elif tank == "B":
        AUTO_PUMP_B = True
    
    GPIO.output(pump_pin, GPIO.LOW)
    
    print(f"Auto dispensing {DEFAULT_AMOUNT}L from tank {tank}")
    
    time.sleep(dispense_time)
    GPIO.output(pump_pin, GPIO.HIGH)
    
    if tank == "A":
        AUTO_PUMP_A = False
    elif tank == "B":
        AUTO_PUMP_B = False
    
    print(f"Finished auto dispensing from tank {tank}")
    
    pass

def handle_command(command):
    
    global AUTO_PUMP_A
    global AUTO_PUMP_B

    try:
        tank = command.get("tank")
        dispense_type = command.get("type")
        
        if dispense_type == "auto" and tank == "A" and AUTO_PUMP_A:
            print(f"Tank {tank} is already auto-dispensing. Ignoring command.")
            publish_alert = {"tank": tank, "alert": "Tank is already auto-dispensing. Ignoring command."}
            client.publish("drfresh/alert", json.dumps(publish_alert))
            publish_command = {"tank": tank, "type": dispense_type, "amount": 0, "status": "failed"}
            client.publish("drfresh/status", json.dumps(publish_command))
            store_command(command, "failed")
            store_alert(tank, "Tank is already auto-dispensing. Ignoring command.")
            return

        if dispense_type == "auto" and tank == "B" and AUTO_PUMP_B:
            print(f"Tank {tank} is already auto-dispensing. Ignoring command.")
            publish_alert = {"tank": tank, "alert": "Tank is already auto-dispensing. Ignoring command."}
            client.publish("drfresh/alert", json.dumps(publish_alert))
            publish_command = {"tank": tank, "type": dispense_type, "amount": 0, "status": "failed"}
            client.publish("drfresh/status", json.dumps(publish_command))
            store_command(command, "failed")
            store_alert(tank, "Tank is already auto-dispensing. Ignoring command.")
            return

        conn = sqlite3.connect('drfresh.db')
        cursor = conn.cursor()
        cursor.execute("SELECT volume FROM tanks WHERE name=?", (tank,))
        result = cursor.fetchone()
        if not result:
            print(f"Tank '{tank}' not found in database.")
            return
        current_volume = result[0]
        
        if current_volume == 0:
            print(f"Tank '{tank}' is empty. Cannot dispense.")
            publish_alert = {"tank": tank, "alert": "Tank is empty. Cannot dispense."}
            client.publish("drfresh/alert", json.dumps(publish_alert))
            publish_command = {"tank": tank, "type": dispense_type, "amount": 0, "status": "failed"}
            client.publish("drfresh/status", json.dumps(publish_command))
            store_command(command, "failed")
            store_alert(tank, "Tank is empty. Cannot dispense.")
            return
        
        if dispense_type == "auto":
            if current_volume < DEFAULT_AMOUNT:
                print(f"Tank '{tank}' is too low to dispense.")
                publish_alert = {"tank": tank, "alert": f"Tank is too low to dispense {DEFAULT_AMOUNT}L"}
                client.publish("drfresh/alert", json.dumps(publish_alert))
                store_command(command, "failed")
                store_alert(tank, f"Tank is too low to dispense {DEFAULT_AMOUNT}L")
                return
            else:
                print(f"Auto-dispensing {DEFAULT_AMOUNT}L from tank '{tank}'")
                auto_dispense(tank)
                store_command(command, "success")
                new_volume = current_volume - DEFAULT_AMOUNT
                cursor.execute("UPDATE tanks SET volume=? WHERE name=?", (new_volume, tank))
                conn.commit()
                print(f"Dispensed from '{tank}'. New volume: {new_volume}L")
                publish_command = {"tank": tank, "type": "auto", "amount": DEFAULT_AMOUNT, "status": "success"}
                client.publish("drfresh/status", json.dumps(publish_command))
                if new_volume < TANK_MIN:
                    print(f"Tank '{tank}' is low. Please refill soon.")
                    publish_alert = {"tank": tank, "alert": "Tank is low. Please refill soon."}
                    client.publish("drfresh/alert", json.dumps(publish_alert))
                    store_alert(tank, "Tank is low. Please refill soon.")
                
        elif dispense_type == "manual":
            
            
            
            
        
    except Exception as e:
        print(f"Error handling command: {e}")
    finally:
        conn.close()
               

        
                
def handle_refill(refill):
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
        publish_command = {"tank": tank, "type": "refill", "amount": volume, "status": "success"}
        client.publish("drfresh/status", json.dumps(publish_command))
    except Exception as e:
        print(f"Error handling refill: {e}")
    finally:
        conn.close()
        
        
def on_connect(client, userdata, flags, rc):
    print(f"Connected with result code {rc}")
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
        
        
def connect_mqtt():
    client = mqtt.Client(client_id)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER, 1883)
    return client

def main():
    global client
    create_database()
    try:
        client = connect_mqtt()
        client.loop_forever()
    finally:
        GPIO.cleanup()

if __name__ == "__main__":
    main()
    
