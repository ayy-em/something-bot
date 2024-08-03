import os

from utils import messages as msgs
from utils import escape_shit as esc


"""
HELLOFRESH IS DEPRECATED
"""

def get_hellofresh_poke_message():
    hellofresh_message = "Привет, Ириндика 👋\nНе забудь выбрать ХеллоуФреш на след неделю!"
    message_text = esc.escape_shit(hellofresh_message)
    return message_text


def poke_ira_for_hellofresh():
    msgs.send_message(text=get_hellofresh_poke_message(), chat_id=os.getenv('IRINDICA_CHAT_ID'), disable_notification=True)
