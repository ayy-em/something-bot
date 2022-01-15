import os
import requests

bot_token = os.environ.get('BOT_TOKEN')
base_url = "https://api.telegram.org/bot{}/".format(bot_token)
test_channel_id = os.environ.get('TEST_CHANNEL_CHAT_ID')


def send_message(message_text, chat_id=test_channel_id, parse_mode='MarkdownV2', disable_notification=True):
    request_url = base_url + "/sendMessage?text={}&chat_id={}&parse_mode={}&disable_notifications={}".format(
        message_text, chat_id, parse_mode, disable_notification)
    r = requests.get(request_url)
    print("@@ Message Sent - Request code: " + str(r.status_code))


def send_test_message():
    test_request_url = base_url + "/sendMessage?chat_id={}&text=TestRequestDone&disable_notification=True".format(test_channel_id)
    r = requests.get(test_request_url)
    print("@@ Test Message - Request code: {}".format(r.status_code))
