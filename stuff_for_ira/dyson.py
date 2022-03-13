from bs4 import BeautifulSoup
import requests
import random
import lxml

from utils import messages as msgs
from utils import escape_shit as esc


ira_messages = [
    "NO Dyson 🙅‍♂️ 🙅‍♀️"
]


def check_dyson_nl():
    page = requests.get('https://www.dyson.nl/haarstyling/dyson-supersonic/overzicht')
    soup = BeautifulSoup(page.content, 'lxml')
    for element in soup.find_all('div', class_='trade-up-item__stock-message'):
        print(element.text)
    return True


def check_and_report_dyson():
    did_it, where_did_it_arrive = check_if_dyson_arrived()
    if did_it:
        poke_ira_for_dyson(where_did_it_arrive)
    else:
        no_dyson_message = random.choice(ira_messages)
        msgs.send_message(text=esc.escape_shit(no_dyson_message), chat_id=159278882)
        

def check_if_dyson_arrived():
    did_it_arrive = check_dyson_nl()
    where_did_it_arrive = 'https://heybaby.com'
    return did_it_arrive, where_did_it_arrive


def poke_ira_for_dyson(where_is_it):
    message_text = create_message_text() + '\n\n' + where_is_it
    msgs.send_message(text=esc.escape_shit(message_text), chat_id=159278882, disable_notification=False)
    print("@@ Poke Ira TikTok complete")


def create_message_text():
    message_text_intro = 'Hey Ira, Dyson tut uje, yo'
    return message_text_intro
