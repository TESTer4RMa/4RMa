import os
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

@dataclass(frozen=True)
class AppConfig:
    """應用程式配置與常數定義 (Single Source of Truth)"""
    
    # --- Infrastructure Settings ---
    LOG_FILE: str = "app.log"
    APP_TITLE: str = "👵 阿嬤的讀信機 v4.1 (Robust TTS)"
    
    # --- API Keys (Environment or File) ---
    GEMINI_API_KEY: Optional[str] = field(default=None)
    YATING_API_KEY: Optional[str] = field(default=None)
    FLET_SECRET_KEY: Optional[str] = field(default=None)

    # --- Gemini Settings ---
    GEMINI_MODELS: List[str] = field(default_factory=lambda: [
        'models/gemini-1.5-flash',
        'models/gemini-1.5-pro',
        'models/gemini-pro'
    ])

    # --- TTS Settings (Yating) ---
    TTS_API_URL: str = "https://tts.api.yating.tw/v2/speeches/short"
    
    # Tuning Parameters (Robert's Optimization)
    TTS_MAX_WORKERS: int = 2       # 降低並發以避免被 API 擋
    TTS_TIMEOUT: int = 15          # 縮短 Timeout，Fail Fast
    TTS_CHUNK_SIZE: int = 80       # 切得更細，單次請求負擔更小
    
    TTS_VOICE_CONFIG: Dict[str, Any] = field(default_factory=lambda: {
        "model": "tai_female_1",
        "speed": 1.0,
        "pitch": 1.0,
        "energy": 1.0
    })
    TTS_AUDIO_CONFIG: Dict[str, str] = field(default_factory=lambda: {
        "encoding": "LINEAR16", 
        "sampleRate": "16K"
    })

    # --- UI Colors ---
    UI_COLORS: Dict[str, str] = field(default_factory=lambda: {
        "app_bgcolor": "#FFF8E1",
        "text_color_primary": "#5D4037",
        "text_color_secondary": "#8D6E63",
        "btn_simple_color": "#2196F3",
        "btn_detailed_color": "#F44336",
        "status_icon_idle": "#FF9800",
        "status_icon_thinking": "#2196F3",
        "status_icon_speaking": "#4CAF50",
        "status_icon_error": "red"
    })

    # --- Prompts ---
    PROMPT_SIMPLE: str = """
    你是一個台語助手。
    任務：看完這張圖片，用「最簡短的台語口語漢字」講重點。
    規則：
    1. 直接講結果，禁止說「這張圖是...」或「重點是...」。
    2. 50字以內。
    3. 嚴格禁止羅馬拼音、注音或解釋，只輸出純漢字。
    """
    
    PROMPT_DETAILED: str = """
    你是一個OCR讀稿機。
    任務：將圖片內容轉成「純台語漢字」。
    嚴格規則：
    1. **絕對禁止**羅馬拼音 (Pe̍h-ōe-jī)、注音或英語。
    2. **絕對禁止**加開場白。
    3. **絕對禁止**解釋含義。
    4. 直接輸出內容，不要分段。
    5. 遇到亂碼跳過。
    """

    # --- Static Assets ---
    SILENT_WAV_B64: str = "UklGRiYAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA="

    @classmethod
    def load_from_env(cls) -> "AppConfig":
        """Factory Method: 從環境變數或檔案載入配置"""
        def _get_key(env_name: str, filename: str) -> Optional[str]:
            key = os.environ.get(env_name)
            if key: return key.strip()
            try:
                if os.path.exists(filename):
                    with open(filename, "r", encoding="utf-8") as f: return f.read().strip()
            except IOError:
                return None
            return None

        def _load_prompt(filename: str, default: str) -> str:
            try:
                if os.path.exists(filename):
                    with open(filename, "r", encoding="utf-8") as f: return f.read().strip()
            except IOError:
                pass
            return default

        # UI Override Logic
        ui_colors = cls.__dataclass_fields__['UI_COLORS'].default_factory()
        if os.path.exists("ui_config.json"):
            try:
                with open("ui_config.json", "r", encoding="utf-8") as f:
                    ui_colors.update(json.load(f))
            except json.JSONDecodeError:
                pass

        return cls(
            GEMINI_API_KEY=_get_key("GEMINI_API_KEY", "Gemini_API.txt"),
            YATING_API_KEY=_get_key("YATING_API_KEY", "Yating_API.txt"),
            FLET_SECRET_KEY=os.environ.get("FLET_SECRET_KEY"),
            UI_COLORS=ui_colors,
            PROMPT_SIMPLE=_load_prompt("prompt_simple.txt", cls.PROMPT_SIMPLE),
            PROMPT_DETAILED=_load_prompt("prompt_detailed.txt", cls.PROMPT_DETAILED)
        )