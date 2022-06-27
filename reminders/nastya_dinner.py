import datetime
import random

from utils import messages as msgs
from utils import escape_shit as esc


def get_nastya_dinner_message(weekday):
    if weekday in range(5):
        message_options = [
            'Настя, ужин!',
            'yo, sign up for dinner now',
            'I wish i did not have to remind you, but you must sign up for dinner now if you want to eat',
            'Do you know what is better than starving? not forgetting to sign up for dinner',
            '🍲 🥗 🥑 🌯 🥩 🥧 - but only if you sign up for dinner in time',
            'Пора бы записаться на ужин, Анастасия',
            '@stasia sign-up for dinner closes at 1600. Do not fuck it up! Ack?',
            'Да-да, как обычно, время записаться на ужин'
        ]
        message_text = esc.escape_shit(random.choice(message_options))
    return message_text


def remind_nastya_about_dinner():
    weekday_today = datetime.datetime.today().weekday()
    message_text = get_nastya_dinner_message(weekday_today)
    msgs.send_message(text=message_text, chat_id=364503815, disable_notification=False)
    print("@@ Poke Nastya dinner complete")
