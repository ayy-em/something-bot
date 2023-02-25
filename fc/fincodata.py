import os
import random
import datetime

import requests

from utils import escape_shit as esc
from utils import messages as msg
from fc import crux_report

finco_chat_id = os.environ.get('FC_GROUP_CHAT_ID')


def get_fc_message(query_name):
    message_text = get_yesterday_text(query_period='yesterday')
    weekday = datetime.datetime.today().weekday()
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
        string_random_text = "Random Activity: " + string_random_activity
        msg.send_message(chat_id=finco_chat_id, text=esc.escape_shit(string_random_text))
    elif weekday == 2:
        text_social = "Okay, one (1️) social post, just announcing a recent article or review, to be scheduled Friday 10:30, alright??"
        msg.send_message(chat_id=finco_chat_id, text=esc.escape_shit(text_social))
    try:
        crux_string = crux_report.get_crux_string()
        message_text += crux_string
    except Exception as e:
        msg.send_message(chat_id=finco_chat_id, text=esc.escape_shit('Whoopsies, fetching CrUX data failed because of this:\n' + str(e)))
    return message_text


def get_yesterday_text(query_period):
    greetings_list = [
        "Good morning! 🌞 Here are the stats. ",
        "Hey-hey people 👋 FinCo stats here. ",
        "Morning my dudes 😎 enjoy your mind-blowing stats",
        "Are you ready to get these sweet **100** Euros a month?! 🤑 ",
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
    try:
        text = text_one + '\n' + get_and_format_ga_data()
    except Exception as e:
        msg.send_message(text=esc.escape_shit('Oh and btw, fetching GA data failed and this is why:'), chat_id=os.environ.get('FC_GROUP_CHAT_ID'))
        msg.send_message(text=esc.escape_shit(str(e)), chat_id=os.environ.get('FC_GROUP_CHAT_ID'))
    return esc.escape_shit(text)


def get_fc_data(query_period):
    """ No longer in use """
    url_to_poke = 'https://fintechcompass.net/api/query/clicks/{}/'.format(query_period)
    r = requests.get(url_to_poke)
    data_json = r.json()
    return data_json


# ToDo: sort out datetime crap with GA
# ToDo: rewrite in a less disgusting way
def get_and_format_ga_data():
    from fc import google_analytics_query as gaq
    ga_list = gaq.get_ga_stats_for_last_week()
    gae_total_counter = 0
    gae_google_counter = 0
    for item in ga_list:
        gae_total_counter = gae_total_counter + int(item[2])
        if item[0] == 'google':
            gae_google_counter = gae_google_counter + int(item[2])
    g_share = 100 * gae_google_counter / gae_total_counter
    g_share_str = str(round(g_share, 1)) + '%'
    ga_text = '**Last 7 Days - GA4**\n\n📈 New website visitors: {}. \n🔍 {} ({}) from Google Search.\n\n'.format(str(gae_total_counter), str(gae_google_counter), g_share_str)
    # Top 5 pages
    try:
        for item in ga_list:
            item.pop(0)
        p_one = ga_list[0][0]
        p_two = ga_list[1][0]
        p_three = ga_list[2][0]
        p_four = ga_list[3][0]
        p_five = ga_list[4][0]
        p_one_counter = p_two_counter = p_three_counter = p_four_counter = p_five_counter = 0
        for item in ga_list:
            if item[0] == p_one:
                p_one_counter += int(item[1])
            if item[0] == p_two:
                p_two_counter += int(item[1])
            if item[0] == p_three:
                p_three_counter += int(item[1])
            if item[0] == p_four:
                p_four_counter += int(item[1])
            if item[0] == p_five:
                p_five_counter += int(item[1])
        bby = [(p_one, p_one_counter), (p_two, p_two_counter), (p_three, p_three_counter), (p_four, p_four_counter), (p_five, p_five_counter)]
        string_to_add = '**Top Pages by New Users** 🔝\n'
        s_one = '#1: {} - {}'.format(str(bby[0][0]), str(bby[0][1]))
        s_two = '#2: {} - {}'.format(str(bby[1][0]), str(bby[1][1]))
        s_three = '#3: {} - {}'.format(str(bby[2][0]), str(bby[2][1]))
        s_four = '#4: {} - {}'.format(str(bby[3][0]), str(bby[3][1]))
        s_five = '#5: {} - {}'.format(str(bby[4][0]), str(bby[4][1]))
        superstring = string_to_add + '\n' + s_one + '\n' + s_two + '\n' + s_three + '\n' + s_four + '\n' + s_five
        ga_text = ga_text + superstring
    except:
        ga_text = ga_text + 'Oh shit, getting top-5 pages failed 🤦‍♂️'
    return ga_text
