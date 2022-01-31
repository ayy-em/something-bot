import requests
from utils import escape_shit as esc


def get_fc_message(param):
    r = requests.get('https://fintechcompass.net/query/clicks/yesterday/')
    data_json = r.json()
    text_one = data_json['period']
    data_json.pop('period')
    clicks_total = len(data_json)
    text_two = '\nTotal: {} clicks.'.format(str(clicks_total))
    text_three = '\n\n'
    for click in data_json:
        text_line = data_json[click]['when'] + ' - ' + data_json[click]['where'] + ' from ' + data_json[click]['uc']
        text_three = text_three + text_line + '\n'
    text = text_one + text_two + text_three
    return esc.escape_shit(text)
