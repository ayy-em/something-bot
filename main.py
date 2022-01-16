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
        tf.send_message(text=vice_text, chat_id="@vice_news")
    print("@@ Vice one complete")
    return "@@ Vice one complete"


@app.route('/channel/didyouknow/post/<thing>')
def process_dyk_one(thing):
    dyk_stuff = dyk.get_dyk(thing)
    if isinstance(dyk_stuff, list):
        tf.send_photo(caption=dyk_stuff[0], photo=dyk_stuff[1], chat_id="@rfn_didyouknow")
    else:
        tf.send_message(text=dyk_stuff, chat_id="@rfn_didyouknow")
    print(f"@@ Did you know {thing} complete")
    return f"@@ Did you know {thing} complete"


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8080, debug=True)
