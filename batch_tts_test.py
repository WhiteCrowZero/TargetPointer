# save as: batch_tts_test.py
import os
import re
from pathlib import Path
from typing import Dict

from dotenv import load_dotenv
from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs

load_dotenv()

API_KEY = os.getenv("ELEVENLABS_API_KEY")
if not API_KEY:
    raise RuntimeError("未找到 ELEVENLABS_API_KEY，请先在环境变量或 .env 中设置。")

client = ElevenLabs(api_key=API_KEY)

PERSON_VOICE_ID_MAP: Dict[str, str] = {
    # "外国绅士": "JBFqnCBsd6RMkjVDRZzb",
    # "唯美女性": "jBpfuIE2acCO8z3wKNLl",
    # "粤语女性": "Xb7hH8MSUJpSbSDYk0k2",
    # "沉稳旁白（男）": "2EiwWnXFnvU5JabPnv8n",
    # "沉稳旁白（女）": "piTKgcLEGmPE4e6mEKli",

}

# 固定测试文案：尽量覆盖中文发音、停顿、数字、英文夹杂、语气
TEST_TEMPLATE = (
    "你好，我是{role}。"
    "这是一段中文语音测试，用来检查发音、停顿、情绪和整体自然度。"
    "现在测试数字，一二三四五六七八九十。"
    "现在测试中英混读，OpenAI，ChatGPT，AI assistant。"
    "今天天气不错，我们开始吧。"
)

# 官方接口支持的常用字段里，这里我们固定这些配置
MODEL_ID = "eleven_flash_v2_5"
LANGUAGE_CODE = "zh"
OUTPUT_FORMAT = "mp3_44100_128"

OUTPUT_DIR = Path("outputs")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def safe_filename(name: str) -> str:
    """把角色名转成安全文件名。"""
    name = re.sub(r"[\\/:*?\"<>|]", "_", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name


def synthesize_one(role: str, voice_id: str) -> Path:
    text = TEST_TEMPLATE.format(role=role)

    audio = client.text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        model_id=MODEL_ID,
        language_code=LANGUAGE_CODE,
        output_format=OUTPUT_FORMAT,
        voice_settings=VoiceSettings(
            stability=0.35,
            similarity_boost=0.75,
            style=0.20,
            use_speaker_boost=True,
            speed=1.0,
        ),
    )

    filename = f"{safe_filename(role)}__{voice_id}.mp3"
    save_path = OUTPUT_DIR / filename

    with open(save_path, "wb") as f:
        for chunk in audio:
            if chunk:
                f.write(chunk)

    return save_path


def main() -> None:
    print(f"开始批量合成，共 {len(PERSON_VOICE_ID_MAP)} 个音色...")
    print(f"输出目录：{OUTPUT_DIR.resolve()}\n")

    success = 0
    failed = 0

    for role, voice_id in PERSON_VOICE_ID_MAP.items():
        try:
            save_path = synthesize_one(role, voice_id)
            print(f"[OK] {role:<8} -> {save_path.name}")
            success += 1
        except Exception as e:
            print(f"[FAIL] {role:<8} -> {voice_id} -> {e}")
            failed += 1

    print("\n合成完成")
    print(f"成功: {success}")
    print(f"失败: {failed}")


if __name__ == "__main__":
    main()

