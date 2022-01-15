from bs4 import BeautifulSoup
import requests
import datetime
import lxml


def get_caption_text():
    # New topic every day
    what_list = ['tech', 'music', 'food', 'drugs', 'entertainment', 'health', 'news']
    wkday_num = datetime.date.today().weekday()
    v_theme = what_list[wkday_num]
    # gets a list from the function below
    list_vice = get_vice_theme(v_theme)
    v_title = escape_shit(list_vice[0])
    v_snippet = escape_shit(list_vice[1])
    v_author = escape_shit(list_vice[2])
    v_link = escape_shit(list_vice[3])
    v_emoji = list_vice[4]
    # Tear the list apart to compile string once again & get an image
    vice_final_content_string = '*' + v_title + '*\n\n' + v_snippet + '\n\n' + "Posted in \\#{} {} \\ \n[Read the article by {}\\.]({})".format(str.capitalize(v_theme), v_emoji, v_author, v_link)
    return vice_final_content_string


def get_vice_theme(theme):
    print('-- LOG - Trying to post vice {} --'.format(theme))
    # i'm totally not a bot
    page = requests.get('https://www.vice.com/en_us/section/' + str(theme))
    emoji_dict = {
        'tech': '💻',
        'music': '🎵',
        'food': '🥐',
        'drugs': '💊',
        'health': '🏥',
        'entertainment': '💃',
        'news': '📰'
    }
    vice_emoji = emoji_dict.get(theme, '📰')
    # Start scraping: soup -> navigate thru page, find what we need, blah blah
    soup = BeautifulSoup(page.content, 'lxml')
    tech_main = soup.find('div', class_='section-page__lede')
    main_news_div = tech_main.find('div', class_='vice-card__content')
    title_text = main_news_div.h3.text
    snippet_text = main_news_div.p.text
    author = main_news_div.div.div.text
    link = main_news_div.h3.a['href']
    return [title_text, snippet_text, author, link, vice_emoji]


def escape_shit(text):
    chars = "_*[]()~`>#+-=|.!"
    # does not escape curly braces so lets hope we don't encounter them
    weird_stuff = '\\'
    for c in chars:
        text = text.replace(c, weird_stuff[0] + c)
    return text
