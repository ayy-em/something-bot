import datetime
import random

from utils import messages as msgs
from utils import escape_shit as esc


tuesday_messages = [
    "Ира, время для ТикТока, однозначно.",
    "🎥 Ira it is TikTok Tuesday, let's gooo",
    "Сегодня вторник и самое время запостить тикток",
    "ты знаешь, что случается по вторникам, так сделай же это",
    "эй слышь, дорогая моя, тикток давай"
]

friday_messages = [
    "👳🏾‍♂️Hello ma'am! A message from sir.\nПора бы уже запостить пятничный Тикток!",
    "👳🏾‍♂️pls👳🏾‍♂️ show👳🏾‍♂️ tiktak👳🏾‍♂️",
    "запости тикток и иди гуляй как свободный человек",
    "Ирочка, а можно тикточек, пожалуйста?"
]


def get_tiktok_poke_message(weekday):
    if weekday == 1:
        message_text = random.choice(tuesday_messages)
    elif weekday == 4:
        message_text = random.choice(friday_messages)
    else:
        message_text = 'wtf, help! bug! help help bug'
    message_text = esc.escape_shit(message_text)
    return message_text


def poke_ira_for_tiktok():
    weekday_today = datetime.datetime.today().weekday()
    message_text = get_tiktok_poke_message(weekday_today)
    msgs.send_message(text=message_text, chat_id=159278882)
    print("@@ Poke Ira TikTok complete")
