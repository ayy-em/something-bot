import os
from channel import channels_vice as vice
from channel import channels_didyouknow as dyk
from utils import telefunctions as tf

from flask import Flask, request

app = Flask(__name__)
bot_token = os.environ.get('BOT_TOKEN')


@app.route('/channel/vice/post/one')
def process_vice_one():
    vice_text = vice.get_vice()
    if vice_text:
        tf.send_message(text=vice_text)
    print("@@ Vice one complete")
    return "@@ Vice one complete"


@app.route('/channel/didyouknow/post/one')
def process_dyk_one():
    # placeholder
    tf.send_test_message()
    print("@@ Did you know one complete")
    return "@@ Did you know one complete"


@app.route('/channel/didyouknow/post/two')
def process_dyk_two():
    # placeholder
    tf.send_test_message()
    print("@@ Did you know two complete")
    return "@@ Did you know two complete"


@app.route('/channel/didyouknow/post/three')
def process_dyk_three():
    # placeholder
    tf.send_test_message()
    print("@@ Did you know three complete")
    return "@@ Did you know three complete"


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8080, debug=True)
