import os
import requests

bot_token = os.environ.get('BOT_TOKEN')
base_url = "https://api.telegram.org/bot{}/".format(bot_token)
test_channel_id = os.environ.get('TEST_CHANNEL_CHAT_ID')

"""
    Important: Escape text before sending it to these functions!!!
"""


def send_message(text, chat_id=test_channel_id, parse_mode='MarkdownV2', disable_notification=True,
                 disable_web_page_preview=False):
    data_json = {
        'text': text,
        'chat_id': chat_id,
        'parse_mode': parse_mode,
        'disable_notification': disable_notification,
        'disable_web_page_preview': disable_web_page_preview
    }
    request_url = base_url + "sendMessage"
    r = requests.post(request_url, data=data_json)
    print(r.text)


def send_photo(caption, photo, chat_id=test_channel_id, parse_mode='MarkdownV2', disable_notification=True):
    data_json = {
        'caption': caption,
        'photo': photo,
        'chat_id': chat_id,
        'parse_mode': parse_mode,
        'disable_notification': disable_notification
    }
    request_url = base_url + "sendPhoto"
    r = requests.post(request_url, data=data_json)


def send_test_message(txt='WhateverTestyTest'):
    test_req_url = base_url + "sendMessage?chat_id={}&text={}&disable_notification=True".format(test_channel_id, txt)
    r = requests.get(test_req_url)
