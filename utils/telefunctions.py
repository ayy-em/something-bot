import os
import requests

bot_token = os.environ.get('BOT_TOKEN')
base_url = "https://api.telegram.org/bot{}/".format(bot_token)
test_channel_id = os.environ.get('TEST_CHANNEL_CHAT_ID')


def send_message(text, chat_id=test_channel_id, parse_mode='MarkdownV2', disable_notification=True, disable_web_page_preview=False):
    data_json = {
        'text': text,
        'chat_id': chat_id,
        'parse_mode': parse_mode,
        'disable_notification': disable_notification,
        'disable_web_page_preview': disable_web_page_preview
    }
    request_url = base_url + "sendMessage"
    r = requests.post(request_url, data=data_json)
    print("@@ Message Request Sent - Response: " + str(r.text))


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
    print("@@ Message Request Sent - Response: " + str(r.text))


def send_test_message():
    test_request_url = base_url + "sendMessage?chat_id={}&text=TestRequestDone&disable_notification=True".format(test_channel_id)
    r = requests.get(test_request_url)
    print("@@ Test Message Request Sent - Response: {}".format(r.text))
