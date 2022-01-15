import requests
import os
import json

reddit_client_id = os.environ["REDDIT_CLIENT_ID"]
reddit_secret = os.environ["REDDIT_SECRET"]
reddit_username = os.environ["REDDIT_USERNAME"]
reddit_password = os.environ["REDDIT_PASSWORD"]


# returns a text
def get_reddit_top_til():
    print('LOG: Got a request to get reddit top til')
    resp = get_reddit_response('/r/todayilearned/top')
    response_json = resp.json()
    reddit_json = response_json['data']
    post_intro = '🧵 Fresh from /r/TodayILearned 📖\n\n'
    post_title = reddit_json['children'][0]['data']['title']
    post_read_text = 'Read comments on Reddit.\n'
    post_link_to_reddit = 'https://reddit.com' + reddit_json['children'][0]['data']['permalink']
    post_read_source_text = '\n\nRead the full thing at the source.\n'
    post_link_to_source = reddit_json['children'][0]['data']['url']
    final_list = [post_intro, post_title, post_read_source_text, post_link_to_source, post_read_text, post_link_to_reddit]
    return final_list


# authorizes, gets a token, uses token to send the actual request
# endpoint format:  "/api/v1/me"
def get_reddit_response(endpoint):
    # authentication stuff
    client_auth = requests.auth.HTTPBasicAuth(reddit_client_id, reddit_secret)
    post_data = {"grant_type": "password", "username": reddit_username, "password": reddit_password, }
    user_agent_string = "script:Telegram_Digest:v0.1 by /u/" + reddit_username
    headers = {"User-Agent": user_agent_string}
    response_first = requests.post("https://www.reddit.com/api/v1/access_token", auth=client_auth, data=post_data, headers=headers)
    # once we get an access_token, we use that with our requests
    auth_string = response_first.json()['token_type'] + ' ' + response_first.json()['access_token']
    auth_headers = {"Authorization": auth_string, "User-Agent": user_agent_string}
    request_url = 'https://oauth.reddit.com' + endpoint
    params_to_send = {"t": "day", "limit": "1"}
    response_to_request = requests.get(request_url, params=params_to_send, headers=auth_headers)
    print('LOG: Successfully returned reddit API response')
    return response_to_request
