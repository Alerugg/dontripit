from flask import Flask, jsonify

app = Flask(__name__)


@app.get('/api/health')
def health():
    return jsonify({'ok': True})


@app.get('/api/python')
def hello_world():
    return '<p>Hello, World!</p>'
