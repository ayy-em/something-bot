import random

from . import strings


def process_text(text):
    response = respond_hi() if check_if_greeting(text) else respond_unknown()
    return response


def check_if_greeting(text):
    return True if text.lower() in strings.hi_msg else False


def respond_unknown():
    return random.choice(strings.unknown_message_response)


def respond_hi():
    return random.choice(strings.hi_msg).capitalize()
