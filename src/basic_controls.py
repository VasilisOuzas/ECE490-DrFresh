#Please note that the code below doesn't take advantage of the relay module and is coded for only one pump
#make sure to run first:
# pip install flask --break-system-packages


from flask import Flask, jsonify
import RPi.GPIO as GPIO

app = Flask(__name__)

PUMP_PIN = 17  # Change to whatever GPIO pin your pump relay is on

GPIO.setmode(GPIO.BCM)
GPIO.setup(PUMP_PIN, GPIO.OUT)
GPIO.output(PUMP_PIN, GPIO.LOW)  # Start with pump OFF

@app.route('/')
def index():
    return open('index.html').read()

@app.route('/pump/on')
def pump_on():
    GPIO.output(PUMP_PIN, GPIO.HIGH)
    return jsonify(status='on')

@app.route('/pump/off')
def pump_off():
    GPIO.output(PUMP_PIN, GPIO.LOW)
    return jsonify(status='off')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
