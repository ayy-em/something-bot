import random

from . import strings
from . import ai_response


def process_text(text):
    response = respond_hi() if check_if_greeting(text) else respond_unknown(text)
    return response


def check_if_greeting(text):
    return True if text.lower() in strings.hi_msg else False


def respond_unknown(text):
    try:
        answer = ai_response.get_ai_response(text)
        return answer
    except Exception as e:
        print('Open AI API error')
        print(e)
        return random.choice(strings.unknown_message_response)


def respond_hi():
    return random.choice(strings.hi_msg).capitalize()
