import os
import openai


def get_ai_response(prompt):
    prompt = prompt[:50] if len(prompt) > 50 else prompt
    openai.api_key = os.getenv('OPEN_AI_API_KEY')
    response = openai.Completion.create(
        model="text-curie-001",
        prompt=prompt,
        temperature=0,
        max_tokens=50
    )
    return response['choices'][0]['text']
