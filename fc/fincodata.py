import requests
from utils import escape_shit as esc


def get_fc_message(parameter):
    r = requests.get('https://fintechcompass.net/query/clicks/yesterday/')
    data_json = r.json()
    text_one = '👋 ' + data_json['period']
    data_json.pop('period')
    clicks_total = len(data_json)
    text_two = '\nTotal: {} users redirected to partners.'.format(str(clicks_total))
    text_three = '\n\n'
    for click in data_json:
        text_line = '- ' + data_json[click]['when'][:5] + ' - ' + data_json[click]['where'] + ' - ' + data_json[click]['uc']
        text_three = text_three + text_line + '\n'
    text = text_one + text_two + text_three
    return esc.escape_shit(text)
