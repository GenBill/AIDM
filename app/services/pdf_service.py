# app/services/pdf_service.py
import os
import base64
from typing import List
from openai import OpenAI
from app.api.deepseek import DeepSeek
from app.schemas import CharacterSheet, MonsterSheet

if os.getenv("OPENAI_API_KEY"):
    MODEL_NAME = "gpt-5.1"
    client = OpenAI()
elif os.getenv("DEEPSEEK_API_KEY"):
    MODEL_NAME = "deepseek-chat" 
    client = DeepSeek()
else:
    raise ValueError("No API key found for OpenAI or DeepSeek")

def encode_image(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode('utf-8')

def parse_character_images(image_bytes_list: List[bytes], user_context: str = "") -> CharacterSheet:
    """
    支持多图解析：
    接收一个 bytes 列表 -> 构造包含多个 image_url 的 Prompt -> 发送给 GPT-4o
    """
    
    # 1. 构造 User Message 的内容列表，先放入文本提示
    user_content = [
        {
            "type": "text", 
            "text": f"Additional Context: {user_context}\n\nPlease parse these character sheet images (there may be multiple pages). Combine information from all pages."
        }
    ]

    # 2. 遍历所有图片，转 Base64 并追加到消息里
    for img_bytes in image_bytes_list:
        base64_image = encode_image(img_bytes)
        user_content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{base64_image}",
                "detail": "high"
            }
        })

    # 3. 系统提示词
    system_prompt = """
    You are an expert D&D 5e Scribe. 
    Analyze the provided character sheet images (Page 1, Page 2, etc.) and extract the data into the structured JSON format.
    Combine spells, inventory, and features from all pages.
    """

    # 4. 调用 GPT-4o Vision
    try:
        completion = client.beta.chat.completions.parse(
            model="gpt-4o-2024-08-06", 
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content} # <-- 这里现在包含了多张图
            ],
            response_format=CharacterSheet,
        )
        
        parsed_result = completion.choices[0].message.parsed
        if not parsed_result:
            raise ValueError("Model refused to process the image.")
            
        return parsed_result

    except Exception as e:
        print(f"Vision API Error: {e}")
        raise e
    
def parse_monster_image(image_bytes: bytes, user_context: str = "") -> MonsterSheet:
    """
    解析怪物图鉴图片 (Stat Block)
    """
    base64_image = encode_image(image_bytes)

    system_prompt = """
    You are an expert D&D 5e Scribe. 
    Analyze the provided Monster Stat Block image and extract the data into the structured JSON format.
    
    VISUAL ANALYSIS RULES:
    1. **Header**: Name, Size, Type, Alignment are usually at the top.
    2. **Attributes**: Extract STR, DEX, CON, INT, WIS, CHA scores (the big numbers).
    3. **Actions**: These are listed under "Actions". Parse attacks carefully (Name, Bonus, Damage).
    4. **Traits**: Passive abilities listed before Actions (e.g., Keen Sight).
    5. **CR**: Challenge Rating.
    """

    try:
        completion = client.beta.chat.completions.parse(
            model="gpt-4o-2024-08-06", 
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": f"Context: {user_context}\n\nPlease parse this monster stat block."},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}",
                            "detail": "high"
                        }
                    }
                ]}
            ],
            response_format=MonsterSheet,
        )
        
        parsed_result = completion.choices[0].message.parsed
        if not parsed_result:
            raise ValueError("Model refused to process the image.")
            
        return parsed_result

    except Exception as e:
        print(f"Vision API Error: {e}")
        raise e