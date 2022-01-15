import strings as strings
import webscraper as ws


def get_reply(msg_sent_to_bot):
    # you are passed a string that's a message from a private tg chat
    # if it has a '/' as first symbol, it's a command
    if msg_sent_to_bot[0] == '/':
        reply_msg = process_command(msg_sent_to_bot)
    else:
        reply_msg = strings.get_reply_string(msg_sent_to_bot)
    return reply_msg


def process_command(command):
    # reply based on a command provided
    if command == "/start":
        reply_msg = strings.get_start_msg()
    elif command == "/fact":
        reply_msg = strings.get_reply_fact()
    elif command == "/weather":
        # reply_msg = ws.get_weather_city('Amsterdam', 'EN')
        reply_msg = "Well, i removed that"
    elif command == "/batavia":
        reply_msg = "Here is the menu: https://imgur.com/a/QE0XuUY"
    else:
        reply_msg = strings.get_reply_string(command)
    return reply_msg
