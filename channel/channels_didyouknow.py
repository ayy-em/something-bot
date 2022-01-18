import requests
from bs4 import BeautifulSoup
import datetime
from utils import reddit as reddit_api
from utils import escape_shit as esc


def get_dyk(thing):
    if thing == 'three':
        fin_text_and_photo = get_this_day_in_history()
    elif thing == 'two':
        fin_text_and_photo = get_wikipedia_dyk()
    else:
        fin_text_and_photo = get_reddit_dyk()
    return fin_text_and_photo


# awoken by get_dyk_post(), returns a list [text, photo_url]
def get_reddit_dyk():
    list_old = reddit_api.get_reddit_top_til()
    list_to_post = []
    for item_old in list_old:
        list_to_post.append(esc.escape_shit(item_old))
    fin_text = '*' + list_to_post[0] + '*_' + list_to_post[1] + '_[' + list_to_post[2] + '](' + list_to_post[3] + ')[' + list_to_post[4] + '](' + list_to_post[5] + ')'
    return fin_text


def get_wikipedia_dyk():
    # top "Did you know..." fact from wiki main
    lets_look_at_this_url = 'https://en.wikipedia.org/wiki/Main_Page'
    page = requests.get(lets_look_at_this_url)
    soup = BeautifulSoup(page.content, 'html.parser')
    div_dyk = soup.find_all('div', {"id": "mp-dyk"})[0]
    ul_dyk_post = div_dyk.find_all('ul')[0]
    # first item from "Did you know" div on wiki main
    li_dyk_post = ul_dyk_post.find_all('li')[0]
    dyk_link = 'https://en.wikipedia.org' + li_dyk_post.find_all('b')[0].a.get('href')
    dyk_link = dyk_link
    dyk_text = str(li_dyk_post.text)[4:]
    dyk_text = dyk_text
    # dont forget to escape any chars
    dyk_fact_text = "📆 *Fact of the day from Wikipedia* 💡\n\nDid you know " + esc.escape_shit(dyk_text) + "\n\n[Learn more on Wikipedia\.](" + esc.escape_shit(dyk_link) + ")"
    # after that, we go for a thumbnail to send with a caption
    page_to_get_highres = 'https://en.wikipedia.org' + div_dyk.div.div.a.get('href')
    page_img = requests.get(page_to_get_highres)
    soup_img = BeautifulSoup(page_img.content, 'html.parser')
    dyk_img_div = soup_img.find('div', class_='fullImageLink')
    dyk_img_img_tag = dyk_img_div.a.get('href')
    dyk_img = 'https:' + dyk_img_img_tag
    list_text_and_photo = [dyk_fact_text, dyk_img]
    return list_text_and_photo


def get_this_day_in_history():
    # gets the page content and returns a string to post
    # TODO: remove it
    lets_look_at_this_url = 'https://www.factmonster.com/dayinhistory'
    page = requests.get(lets_look_at_this_url)
    soup = BeautifulSoup(page.content, 'html.parser')
    div_fact = soup.find_all('div', {"class": "history-current-events"})[0]
    facts_string = ''
    ul_fact = div_fact.ul
    for fact_item in ul_fact.children:
        item_year = fact_item.h3.text
        desc_year = fact_item.p.text
        string_to_add = '*' + item_year + '*: ' + esc.escape_shit(desc_year) + '\n'
        facts_string += string_to_add
    text_intro = '📆 This Day In History 📜'
    final_string = '*' + text_intro + '*\n\n' + facts_string
    return final_string
