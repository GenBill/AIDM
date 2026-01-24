import os
from openai import OpenAI

class DeepSeek(OpenAI):
    def __init__(self):
        super().__init__(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url='https://api.deepseek.com')

