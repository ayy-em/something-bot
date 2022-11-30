import os
import openai


def get_ai_response(prompt):
    openai.api_key = os.getenv('OPEN_AI_API_KEY')
    response = openai.Completion.create(
        model="text-curie-001",
        prompt="Hello",
        temperature=0,
        max_tokens=50
    )
    return response['choices'][0]['text']