from bs4 import BeautifulSoup
import requests
import random
import lxml

from utils import messages as msgs
from utils import escape_shit as esc


def check_and_report_dyson():
    did_it = check_dyson_nl()
    if did_it:
        poke_ira_for_dyson('https://www.dyson.nl/haarstyling/dyson-supersonic/overzicht')
    else:
        no_dyson_message = "NO Dyson 🙅‍♂️ 🙅‍♀️"
        msgs.send_message(text=esc.escape_shit(no_dyson_message), chat_id=159278882)


def check_dyson_nl():
    """
    :return: True if found, False if not
    """
    headers = requests.utils.default_headers()
    ua_list = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:98.0) Gecko/20100101 Firefox/98.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:98.0) Gecko/20100101 Firefox/98.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.2 Safari/605.1.15',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.11; rv:78.0) Gecko/20100101 Firefox/78.0',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.51 Safari/537.36'
    ]
    ua_for_request = random.choice(ua_list)
    headers.update(
        {
            'User-Agent': ua_for_request
        }
    )
    page = requests.get('https://www.dyson.nl/haarstyling/dyson-supersonic/overzicht', headers=headers)
    soup = BeautifulSoup(page.content, 'lxml')
    dyson_detected = False
    for element in soup.find_all('div', class_='trade-up-item__stock-message'):
        if element.text != 'Momenteel niet op voorraad':
            dyson_detected = True
        else:
            pass
    return dyson_detected


def poke_ira_for_dyson(where_is_it):
    message_text = 'Hey Ira, Dyson tut uje, yo' + '\n\n' + where_is_it
    msgs.send_message(text=esc.escape_shit(message_text), chat_id=159278882, disable_notification=False)
    print("@@ Poke Ira TikTok complete")
