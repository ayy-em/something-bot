from bs4 import BeautifulSoup
import requests
import random
import lxml

from utils import messages as msgs
from utils import escape_shit as esc


def check_and_report_dyson():
    print('trying my best YO')
    did_it = check_dyson_nl()
    msgs.send_test_message('Checked Dyson and did_it equals ' + str(did_it))
    if did_it:
        poke_ira_for_dyson('https://www.dyson.nl/haarstyling/dyson-supersonic/overzicht')
    else:
        no_dyson_message = "NO Dyson 🙅‍♂️ 🙅‍♀️"
        msgs.send_message(text=esc.escape_shit(no_dyson_message), chat_id=159278882)


def check_dyson_nl():
    """
    :return: True if found, False if not
    """
    print('@@ Attempting to check Dyson NL')
    headers = requests.utils.default_headers()
    headers.update(
        {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:98.0) Gecko/20100101 Firefox/98.0'
        }
    )
    # ToDo:
    # ToDo: https://www.scrapingbee.com/blog/python-requests-proxy/
    dyson_detected = False
    try:
        page = requests.get('https://www.dyson.nl/haarstyling/dyson-supersonic/overzicht', headers=headers, timeout=180)
        soup = BeautifulSoup(page.content, 'lxml')
        for element in soup.find_all('div', class_='trade-up-item__stock-message'):
            if element.text != 'Momenteel niet op voorraad':
                dyson_detected = True
            else:
                pass
    except Exception as exc:
        msgs.send_test_message('I ran into the following exception:\n\n' + str(exc))
    return dyson_detected


def poke_ira_for_dyson(where_is_it):
    message_text = 'Hey Ira, Dyson tut uje, yo!!\n\n' + where_is_it
    msgs.send_message(text=esc.escape_shit(message_text), chat_id=159278882, disable_notification=False)
    print("@@ Poke Ira Dyson complete")
