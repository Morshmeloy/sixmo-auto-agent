import os
from dotenv import load_dotenv
import openai

load_dotenv()
client = openai.OpenAI(
    base_url="https://models.inference.ai.azure.com",
    api_key=os.getenv("GITHUB_TOKEN")
)

try:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=10
    )
    print("Токен работает, ответ:", response.choices[0].message.content)
except Exception as e:
    print("Ошибка:", e)