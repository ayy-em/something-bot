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
    if weekday == 1 or weekday == 4:
        activity_num = random.randint(1, 5)
        if activity_num == 1:
            string_random_activity = 'Post one backlink somewhere - wikipedia, Revolut forums, you name it!'
            # ToDo: parse sitemap to get bank names
        elif activity_num == 2:
            string_random_activity = 'Send like two emails for potential backlink prospects.'
        elif activity_num == 3:
            string_random_activity = 'Pick an invest platform/app at random, check if the stats in the model are up-to-date and improve the review somehow!'
        elif activity_num == 4:
            string_random_activity = 'Pick a random bank, and check if the terms/fees/products in the model are up-to-date.'
        else:
            string_random_activity = 'Respond to one HARO request.'
        string_random_text = "It's random activity for random person day!\nActivity: " + string_random_activity + '\nAnd the random person to do that today is... {}!'.format(str(random.choice(possible_doers)))
        msg.send_message(chat_id=finco_chat_id, text=esc.escape_shit(string_random_text))
    elif weekday == 2:
        text_social = "Okay, one (1️) social post, just announcing a recent article or review, to be scheduled Friday 10:30, alright??\nOkay, let's throw the dice! 🎲\nAlright! This week's post is by the star SMM guy...\nAnd his name is ... {}!".format(str(random.choice(possible_doers)))
        msg.send_message(chat_id=finco_chat_id, text=esc.escape_shit(text_social))

    return message_text


def get_yesterday_text(query_period):
    data_json = get_fc_data(query_period)
    greetings_list = [
        "Good morning! 🌞 Here are the stats. ",
        "Hey-hey people 👋 FinCo stats here. ",
        "Morning my dudes 😎 enjoy your mind-blowing stats",
        "Are you ready to get these sweet 10 Euros a month?! 🤑 ",
        "Alright, let's get down to business 💼 ",
        "Another day, another dollar Ali pays you via Everflow.",
        "Did you post something on social today? 👀 ",
        "ayy lmao 👽 Juicy new batch of totally wrong stats arrived! ",
        "Rise and shine, comrades! There's labor to be done today.",
        "Salutations, gentlemen!",
        "Ok, new day, is it the one where you spend literally five minutes doing reachout for backlinks?!",
        "G'Morning. FYI: Your competitors are writing new exciting niche high-traffic SEO-polished content RIGHT NOW.",
        "Start your day with super-inaccurate stats!"
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
        text_two = "\n\nBackend redirects yesterday: {}. Totally reliable data, automagically.\n".format(str(clicks_total))
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
    url_to_poke = 'https://fintechcompass.net/api/query/clicks/{}/'.format(query_period)
    r = requests.get(url_to_poke)
    data_json = r.json()
    return data_json


def get_and_format_ga_data():
    from fc import google_analytics_query as gaq
    ga_list = gaq.get_ga_stats_for_last_week()
    gae_total_counter = 0
    gae_google_counter = 0
    for item in ga_list:
        gae_total_counter = gae_total_counter + int(item[2])
        if item[0] == 'google':
            gae_google_counter = gae_google_counter + 1
    ga_text = '⚠️ Updated not-bullshit GA4 report!  ⚠️\nLast Week: {} new users. {} from Google Search.'.format(str(gae_total_counter), str(gae_google_counter))
    # ToDo: sort out datetime crap
    return ga_text
