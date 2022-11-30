import os
import openai


def get_ai_response(prompt):
    if prompt[:19] == '@SomethingReallyBot':
        prompt = prompt[20:95] if len(prompt[20:]) > 75 else prompt[20:]
    else:
        prompt = prompt[:75] if len(prompt) > 75 else prompt
    if prompt[:5] == 'image':
        if prompt[:10] == 'image of a':
            prompt = prompt[11:]
        elif prompt[:8] == 'image of':
            prompt = prompt[9:]
        else:
            prompt = prompt[6:]
        response = openai.Image.create(
            prompt=prompt,
            n=1,
            size="512x512"
        )
        return response['data'][0]['url'], 'image_url'
    else:
        openai.api_key = os.getenv('OPEN_AI_API_KEY')
        response = openai.Completion.create(
            model="text-davinci-003",
            prompt=prompt,
            temperature=0.35,
            max_tokens=100
        )
        return response['choices'][0]['text'], 'text'

