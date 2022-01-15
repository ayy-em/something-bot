import requests

from flask import Flask, request

app = Flask(__name__)


@app.route('/', methods=['GET'])
def get_root():
    r = requests.get(
        "https://api.telegram.org/bot1651613683:AAEsfNziIczgMWs122wYUyES_A-boBwK5SQ/sendMessage?chat_id=-1001626784462&text={}".format(
            str(request.get_data())))
    return "Hello there"


@app.route('/', methods=['POST'])
def post_root():
    r = requests.get("https://api.telegram.org/bot1651613683:AAEsfNziIczgMWs122wYUyES_A-boBwK5SQ/sendMessage?chat_id=-1001626784462&text={}".format(str(request.get_data())))
    print("That post json here is: " + str(request.get_json()))
    return "Hello you and your post call"


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8080, debug=True)
