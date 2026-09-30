from flask import Flask

app = Flask(__name__)


@app.route("/")
def health():
    return "Kitsu is alive!", 200