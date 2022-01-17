import os
from channel import channels_vice as vice
from channel import channels_didyouknow as dyk
from utils import messages as msgs
from utils import update_handler as uh

from flask import Flask, request

app = Flask(__name__)
bot_token = os.environ.get('BOT_TOKEN')


@app.route('/', methods=['POST'])
def process_post_update():
    if request.get_json():
        processed_update = uh.process_update(request.get_json())
        if isinstance(processed_update, uh.TextMessageUpdate):
            if processed_update.message_destination == 'direct':
                msgs.send_message(text=processed_update.text_message_text, chat_id=processed_update.message_chat_from)
            elif processed_update.message_destination == 'groupchat':
                test_message_text = 'I got sent this in a group: ' + processed_update.text_message_text
                msgs.send_test_message(txt=test_message_text)
            elif processed_update.message_destination == 'channel':
                test_message_text = 'I got sent this in a channel: ' + processed_update.text_message_text
                msgs.send_test_message(txt=test_message_text)
    print("@@ Processing update complete")
    return "@@ Processing update complete"


@app.route('/channel/vice/post/one')
def process_vice_one():
    vice_text = vice.get_vice()
    if vice_text:
        msgs.send_message(text=vice_text, chat_id="@vice_news")
    print("@@ Vice one complete")
    return "@@ Vice one complete"


@app.route('/channel/didyouknow/post/<thing>')
def process_dyk_one(thing):
    dyk_stuff = dyk.get_dyk(thing)
    if isinstance(dyk_stuff, list):
        msgs.send_photo(caption=dyk_stuff[0], photo=dyk_stuff[1], chat_id="@rfn_didyouknow")
    else:
        msgs.send_message(text=dyk_stuff, chat_id="@rfn_didyouknow")
    print(f"@@ Did you know {thing} complete")
    return f"@@ Did you know {thing} complete"


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8080, debug=True)
