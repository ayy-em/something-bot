import os
import openai


openai.api_key = os.getenv('OPEN_AI_API_KEY')


def get_ai_response(prompt):
    if prompt[:19] == '@SomethingReallyBot':
        prompt = prompt[20:95] if len(prompt[20:]) > 75 else prompt[20:]
    else:
        prompt = prompt[:75] if len(prompt) > 75 else prompt
    if prompt[:5].lower() == 'image':
        if prompt[:10].lower() == 'image of a':
            prompt = prompt[11:]
        elif prompt[:8].lower() == 'image of':
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
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.75,
            frequency_penalty=0.8,
            max_tokens=200
        )
        return response['choices'][0]['message']['content'], 'text'
