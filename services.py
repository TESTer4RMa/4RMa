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
        """獲取可用模型列表，若 API 失敗則回傳設定檔中的預設列表"""
        try:
            # 嘗試動態獲取
            api_models = [
                m.name for m in genai.list_models() 
                if 'generateContent' in m.supported_generation_methods
            ]
            # 簡單排序：Flash 優先
            return sorted(api_models, key=lambda name: 0 if 'flash' in name.lower() else 1)
        except Exception as e:
            self.logger.warning(f"無法動態列出模型，使用預設列表: {e}")
            return self.models

    @time_it
    def get_intent(self, image_bytes: bytes, prompt: str) -> str:
        """發送圖片至 Gemini 進行辨識 (Failover 機制)"""
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
        self.chunk_size = config.TTS_CHUNK_SIZE  # New Config
        
        # 語音參數
        self.voice_config = config.TTS_VOICE_CONFIG
        self.audio_config = config.TTS_AUDIO_CONFIG

        if not self.api_key:
            self.logger.warning("⚠️ Yating API Key 未設定，TTS 服務將不可用。")

    def _split_text(self, text: str, limit: int) -> List[str]:
        """將長文本切割為適合 API 的片段 (支援 Config Limit)"""
        # 使用正則表達式保留標點符號作為分割依據
        sentences = re.split(r'(。|，|\n|；|！|？)', text)
        chunks, current = [], ""
        
        for i in range(0, len(sentences)-1, 2):
            part = sentences[i] + sentences[i+1]
            if len(current) + len(part) < limit:
                current += part
            else:
                if current: chunks.append(current)
                current = part 
        
        if len(sentences) % 2 != 0:
            current += sentences[-1]
        
        if current:
            chunks.append(current)
        return chunks

    def _download_chunk(self, text_chunk: str, index: int) -> Optional[Tuple[int, bytes]]:
        """下載單一音訊片段 (Retry Pattern)"""
        payload = {
            "input": {"text": text_chunk, "type": "text"},
            "voice": self.voice_config,
            "audioConfig": self.audio_config
        }
        headers = {"Content-Type": "application/json", "key": self.api_key}

        # 增加一次重試機會，因為片段小，重試成本低
        for attempt in range(4):
            try:
                response = requests.post(
                    self.api_url, 
                    headers=headers, 
                    json=payload, 
                    timeout=self.timeout
                )
                
                if response.status_code in [200, 201]:
                    data = response.json()
                    content = data.get("audioContent")
                    if content:
                        return (index, base64.b64decode(content))
                
                self.logger.warning(f"Chunk {index} API 錯誤 (Code: {response.status_code})")
                
            except requests.RequestException as e:
                self.logger.warning(f"Chunk {index} 網路錯誤 (嘗試 {attempt+1}/4): {e}")
        
        self.logger.error(f"❌ Chunk {index} 在多次嘗試後宣告失敗。")
        return None

    @time_it
    def synthesize(self, text: str) -> bytes:
        if not text or not text.strip():
            raise ValueError("TTS 輸入文字為空")

        # 使用 Config 中的 Chunk Size
        chunks = self._split_text(text, limit=self.chunk_size)
        self.logger.info(f"文字已切分為 {len(chunks)} 個片段 (Limit: {self.chunk_size})")

        audio_parts: Dict[int, bytes] = {}

        # 使用 ThreadPool 進行並發下載
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [
                executor.submit(self._download_chunk, chunk, i) 
                for i, chunk in enumerate(chunks)
            ]
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    idx, data = result
                    audio_parts[idx] = data

        # [CRITICAL] 完整性檢查：Fail Fast
        if len(audio_parts) != len(chunks):
            missing = len(chunks) - len(audio_parts)
            error_msg = f"語音合成不完整！遺失 {missing}/{len(chunks)} 個片段。"
            self.logger.error(error_msg)
            raise RuntimeError(error_msg)

        # 記憶體內合併 WAV (In-Memory Processing)
        output_buffer = io.BytesIO()
        sorted_keys = sorted(audio_parts.keys())
        
        try:
            with wave.open(output_buffer, 'wb') as wav_out:
                for i, idx in enumerate(sorted_keys):
                    part_bytes = audio_parts[idx]
                    with wave.open(io.BytesIO(part_bytes), 'rb') as wav_in:
                        if i == 0:
                            wav_out.setparams(wav_in.getparams())
                        wav_out.writeframes(wav_in.readframes(wav_in.getnframes()))
        except wave.Error as e:
            self.logger.error(f"WAV 合併失敗: {e}")
            raise RuntimeError(f"音訊合併失敗: {e}")

        self.logger.info("✅ 語音合成與合併完成")
        return output_buffer.getvalue()