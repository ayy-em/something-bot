import os
import random
import datetime

import requests

from utils import escape_shit as esc
from utils import messages as msg


finco_chat_id = os.environ.get('FC_GROUP_CHAT_ID')


def get_fc_message(query_name):
    message_text = get_yesterday_text(query_period='yesterday')
    weekday = datetime.datetime.today().weekday()
    possible_doers = ['@ayy_em', '@aaronhzl']
    if weekday == 1:
        text_billing = "Hey yo @ayy_yo check if this month's billing is not crazy here: https://console.cloud.google.com/billing/01F463-60D020-C5CD47/reports;chartType=STACKED_BAR;grouping=GROUP_BY_SKU?project=bank-comparison-website."
        msg.send_message(chat_id=finco_chat_id, text=esc.escape_shit(text_billing))
    elif weekday == 2:
        text_social = "Picking a random person to add a news article and post it on social.\nThis week the lucky guy is ...{}!".format(str(random.choice(possible_doers)))
        msg.send_message(chat_id=finco_chat_id, text=esc.escape_shit(text_social))
    elif weekday == 3:
        text_haro = 'Random pick to check out fresh HARO mails, find us one thing to comment on and reach out to the reporter. Today is the day for... {}!'.format(str(random.choice(possible_doers)))
        msg.send_message(chat_id=finco_chat_id, text=esc.escape_shit(text_haro))
    return message_text


def get_yesterday_text(query_period):
    data_json = get_fc_data(query_period)
    greetings_list = [
        "Good morning! 🌞 Here are the stats. ",
        "Heya 👋 FinCo stats here. ",
        "Morning my dudes 😎 enjoy your mind-blowing stats",
        "Are you ready to get these sweet 10 Euros a month?! 🤑 ",
        "Let's get down to business 💼 ",
        "Did you post something on social today? 👀 ",
        "ayy lmao 👽 Fresh stats arrived! ",
        "Ciao miei cari amici! 🤌 🤌 🤌 ",
        "Hello there boyz! ✌ ",
        "Welcome to fresh stats!"
    ]
    text_one = random.choice(greetings_list) + "\n"
    text_one = text_one + data_json['period']
    data_json.pop('period')
    clicks_total = len(data_json)
    if 0 < clicks_total < 8:
        text_two = "\n\nRedirects to partners: {}. Here's a list:\n".format(str(clicks_total))
        for click in data_json:
            text_line = '- ' + data_json[click]['when'][:5] + ' - ' + data_json[click]['where'] + ' - ' + \
                        data_json[click]['uc']
            text_two = text_two + text_line + '\n'
    elif clicks_total >= 8:
        text_two = "\n\nRedirects to partners: {}.\n".format(str(clicks_total))
    else:
        text_two = '\nZero redirects to partners. Feels bad, man. 😔\n'

    text = text_one + text_two
    try:
        text = text + '\n🤖 ' + get_and_format_ga_data()
    except Exception as e:
        msg.send_message(text=esc.escape_shit('Oh and btw, fetching GA data failed and this is why:'), chat_id=os.environ.get('FC_GROUP_CHAT_ID'))
        msg.send_message(text=esc.escape_shit(str(e)), chat_id=os.environ.get('FC_GROUP_CHAT_ID'))
    return esc.escape_shit(text)


def get_fc_data(query_period):
    url_to_poke = 'https://fintechcompass.net/query/clicks/{}/'.format(query_period)
    r = requests.get(url_to_poke)
    data_json = r.json()
    return data_json


def get_and_format_ga_data():
    from fc import google_analytics_query as gaq
    ga_list = gaq.get_ga_stats_for_yesterday()
    gae_total_counter = 0
    gae_google_counter = 0
    for item in ga_list:
        gae_total_counter = gae_total_counter + int(item[2])
        if item[0] == 'google':
            gae_google_counter = gae_google_counter + 1
    ga_text = 'GA4: {} visitors, of which {} from Google search.'.format(str(gae_total_counter), str(gae_google_counter))
    return ga_text
