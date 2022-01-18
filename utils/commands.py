import random
from . import strings


def process_command(command):
    # reply based on a command provided
    if command == "/start":
        reply_msg = get_start_message()
    elif command == "/fact":
        reply_msg = get_reply_fact()
    elif command == "/weather":
        reply_msg = "I removed that feature."
    elif command == "/batavia":
        reply_msg = "Here is a link to the menu: https://www.instagram.com/p/CACvNqRnalJ/"
    else:
        reply_msg = "No idea how to respond to that command, mate."
    reply_msg = reply_msg
    return reply_msg


# when you message the bot "/start", it replies with that string
def get_start_message():
    reply_msg_start = strings.start_message_response
    return reply_msg_start


# when commanded, returns a string with a random fact
def get_reply_fact():
    reply_msg_text = random.choice(strings.fun_fact)
    return reply_msg_text
