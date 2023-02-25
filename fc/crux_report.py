import requests
import os

API_KEY = os.environ.get('CRUX_API_KEY')
headers = {
    'Content-Type': 'application/json',
    'Accept': 'application/json'
}

# best-shared-bank-accounts/
def get_crux_api_report(form_factor='DESKTOP'):
    url_to_poke = 'https://chromeuxreport.googleapis.com/v1/records:queryRecord?key={}'.format(API_KEY)
    if form_factor == 'DESKTOP':
        data = '{"origin": "https://fintechcompass.net/", "formFactor": "DESKTOP", "metrics": ["first_contentful_paint", "largest_contentful_paint", "cumulative_layout_shift"]}'
    else:
        data = '{"origin": "https://fintechcompass.net/", "formFactor": "PHONE", "metrics": ["first_contentful_paint", "largest_contentful_paint", "cumulative_layout_shift"]}'
    r = requests.post(url_to_poke, data=data, headers=headers)
    good_cls = r.json()['record']['metrics']['cumulative_layout_shift']['histogram'][0]['density']
    subpar_cls = r.json()['record']['metrics']['cumulative_layout_shift']['histogram'][1]['density']
    poor_cls = r.json()['record']['metrics']['cumulative_layout_shift']['histogram'][2]['density']
    good_fcp = r.json()['record']['metrics']['first_contentful_paint']['histogram'][0]['density']
    subpar_fcp = r.json()['record']['metrics']['first_contentful_paint']['histogram'][1]['density']
    poor_fcp = r.json()['record']['metrics']['first_contentful_paint']['histogram'][2]['density']
    good_lcp = r.json()['record']['metrics']['largest_contentful_paint']['histogram'][0]['density']
    subpar_lcp = r.json()['record']['metrics']['largest_contentful_paint']['histogram'][1]['density']
    poor_lcp = r.json()['record']['metrics']['largest_contentful_paint']['histogram'][2]['density']
    final_string = '{} - Rolling 28 days\nCLS - Good: {}%, Subpar: {}%, Poor: {}%\nFCP - Good: {}%, Subpar: {}%, Poor: {}%\nLCP - Good: {}%, Subpar: {}%, Poor: {}%'.format(
        form_factor.capitalize(), 
        round(good_cls*100, 2),
        round(subpar_cls*100, 2),
        round(poor_cls*100, 2),
        round(good_fcp*100, 2),
        round(subpar_fcp*100, 2),
        round(poor_fcp*100, 2),
        round(good_lcp*100, 2),
        round(subpar_lcp*100, 2),
        round(poor_lcp*100, 2),
    )
    return str(final_string)


def get_crux_string():
    crux_header = '\n\n**Chrome UX Report - Web Vitals Data**\n\n'
    crux_desktop = get_crux_api_report(form_factor='DESKTOP')
    crux_mobile = get_crux_api_report(form_factor='PHONE')
    crux_string = crux_header + crux_desktop + '\n\n' + crux_mobile
    return crux_string
