import requests
import base64
import re
import io
import wave
import concurrent.futures
import google.generativeai as genai
from typing import List, Optional, Tuple, Dict, Any

from config import AppConfig
from utils import time_it, get_logger

class GeminiService:
    def __init__(self, config: AppConfig):
        self.logger = get_logger()
        if not config.GEMINI_API_KEY:
            self.logger.warning("⚠️ Gemini API Key 未設定，AI 辨識服務將不可用。")
        else:
            genai.configure(api_key=config.GEMINI_API_KEY)
        self.models = config.GEMINI_MODELS

    def _get_available_models(self) -> List[str]:
        try:
            api_models = [
                m.name for m in genai.list_models() 
                if 'generateContent' in m.supported_generation_methods
            ]
            return sorted(api_models, key=lambda name: 0 if 'flash' in name.lower() else 1)
        except Exception as e:
            self.logger.warning(f"無法動態列出模型，使用預設列表: {e}")
            return self.models

    @time_it
    def get_intent(self, image_bytes: bytes, prompt: str) -> str:
        candidate_models = self._get_available_models()
        last_error = None
        for model_name in candidate_models:
            try:
                self.logger.info(f"嘗試使用模型: {model_name}")
                model = genai.GenerativeModel(model_name)
                response = model.generate_content([
                    prompt, 
                    {'mime_type': 'image/jpeg', 'data': image_bytes}
                ])
                if response.text:
                    self.logger.info(f"✅ 模型 {model_name} 辨識成功")
                    return response.text
            except Exception as e:
                self.logger.warning(f"⚠️ 模型 {model_name} 失敗: {str(e)}")
                last_error = e
                continue 
        raise RuntimeError(f"所有模型嘗試皆失敗。最後錯誤: {str(last_error)}")


class YatingTTSService:
    def __init__(self, config: AppConfig):
        self.logger = get_logger()
        self.api_key = config.YATING_API_KEY
        self.api_url = config.TTS_API_URL
        self.max_workers = config.TTS_MAX_WORKERS
        self.timeout = config.TTS_TIMEOUT
        self.chunk_size = config.TTS_CHUNK_SIZE
        self.voice_config = config.TTS_VOICE_CONFIG
        self.audio_config = config.TTS_AUDIO_CONFIG

        if not self.api_key:
            self.logger.warning("⚠️ Yating API Key 未設定，TTS 服務將不可用。")

    def _split_text(self, text: str, limit: int) -> List[str]:
        sentences = re.split(r'(。|，|\n|；|！|？)', text)
        chunks, current = [], ""
        for part in sentences:
            if len(current) + len(part) < limit:
                current += part
            else:
                if current: chunks.append(current)
                current = part 
        if current:
            chunks.append(current)
        return chunks

    def _download_chunk(self, text_chunk: str, index: int) -> Optional[Tuple[int, bytes]]:
        payload = {
            "input": {"text": text_chunk, "type": "text"},
            "voice": self.voice_config,
            "audioConfig": self.audio_config
        }
        headers = {"Content-Type": "application/json", "key": self.api_key}

        for attempt in range(3):
            try:
                response = requests.post(
                    self.api_url, headers=headers, json=payload, timeout=self.timeout
                )
                if response.status_code in [200, 201]:
                    data = response.json()
                    content = data.get("audioContent")
                    if content:
                        raw_bytes = base64.b64decode(content)
                        if raw_bytes.startswith(b'RIFF'):
                            return (index, raw_bytes)
                        else:
                            self.logger.warning(f"Chunk {index} 回傳格式異常 (非 RIFF)")
                self.logger.warning(f"Chunk {index} API 錯誤 (Code: {response.status_code})")
            except Exception as e:
                self.logger.warning(f"Chunk {index} 嘗試 {attempt+1} 失敗: {e}")
        
        self.logger.error(f"❌ Chunk {index} 下載徹底失敗")
        return None

    def _merge_wav_bytes(self, audio_parts: Dict[int, bytes]) -> bytes:
        """
        使用標準 wave 模組安全合併 WAV 檔案，並確保 Header 完整性。
        """
        sorted_keys = sorted(audio_parts.keys())
        if not sorted_keys:
            return b""

        output_buffer = io.BytesIO()

        try:
            # 1. 讀取第一個片段以獲取參數
            first_wav_data = audio_parts[sorted_keys[0]]
            with wave.open(io.BytesIO(first_wav_data), 'rb') as first_wav:
                params = first_wav.getparams()
                
                # 2. 寫入新的 WAV (這會重新計算並寫入標準 Header)
                with wave.open(output_buffer, 'wb') as out_wav:
                    out_wav.setparams(params)
                    
                    # 寫入第一段
                    out_wav.writeframes(first_wav.readframes(first_wav.getnframes()))
                    
                    # 寫入後續片段 (如果有的話)
                    for key in sorted_keys[1:]:
                        with wave.open(io.BytesIO(audio_parts[key]), 'rb') as part_wav:
                            if part_wav.getparams()[:4] != params[:4]:
                                self.logger.warning(f"Chunk {key} 音訊參數不一致，跳過合併")
                                continue
                            out_wav.writeframes(part_wav.readframes(part_wav.getnframes()))
            
            return output_buffer.getvalue()

        except wave.Error as e:
            self.logger.error(f"WAV 處理失敗 (Wave Error): {e}")
            # Fallback: 萬一 wave 解析失敗，回傳原始 bytes 避免當機
            return audio_parts[sorted_keys[0]]
        except Exception as e:
            self.logger.error(f"WAV 合併發生未預期錯誤: {e}")
            raise e

    @time_it
    def synthesize(self, text: str) -> bytes:
        if not text or not text.strip():
            raise ValueError("TTS 輸入文字為空")

        chunks = self._split_text(text, limit=self.chunk_size)
        self.logger.info(f"文字已切分為 {len(chunks)} 個片段")

        audio_parts: Dict[int, bytes] = {}

        # 即使只有一個片段，我們也通過 download -> merge 流程
        # 原因：_merge_wav_bytes 會使用 wave 模組重新生成 Header
        # 這能修復 API 可能回傳的不標準 Header (例如 File Size 錯誤)
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [
                executor.submit(self._download_chunk, chunk, i) 
                for i, chunk in enumerate(chunks)
            ]
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    audio_parts[result[0]] = result[1]

        if len(audio_parts) != len(chunks):
            raise RuntimeError(f"語音合成不完整！遺失 {len(chunks) - len(audio_parts)} 個片段")

        # 這裡的 "Merge" 實際上也扮演了 "Sanitize" (淨化) 的角色
        final_wav = self._merge_wav_bytes(audio_parts)
        self.logger.info(f"✅ 語音合成完成 (經 Header 校正)，總大小: {len(final_wav)} bytes")
        return final_wav
