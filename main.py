import os
from channel import channels_vice as vice
from channel import channels_didyouknow as dyk
from fc import fincodata as fcd
from utils import messages as msgs
from utils import update_handler as uh
from utils import response as rsp
from stuff_for_ira import tiktok as tt
from stuff_for_ira import dyson as dys

from flask import Flask, request

app = Flask(__name__)
bot_token = os.environ.get('BOT_TOKEN')


@app.route('/', methods=['POST'])
def process_post_update():
    if request.get_json():
        processed_update = uh.process_update(request.get_json())
        rsp.respond_to(processed_update)
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


@app.route('/poke/bettyford/tiktok')
def poke_bettyford_tiktok():
    print('@@ Starting the Tiktok flow')
    tt.poke_ira_for_tiktok()
    return "@@ Poke Ira TikTok complete"


@app.route('/check/bettyford/dyson')
def check_for_dyson():
    print('@@ Starting the Dyson flow')
    dys.check_and_report_dyson()
    return '@@ Dyson check complete'


@app.route('/finco/query/<query_name>')
def post_yesterday_finco_stats(query_name='yesterday'):
    text = fcd.get_fc_message(query_name)
    chat_id = os.environ.get('FC_GROUP_CHAT_ID')
    msgs.send_message(text=text, chat_id=chat_id)
    print("@@ FinCo query - Yesterday complete")
    return "@@ FinCo query - Yesterday complete"


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8080, debug=True)
