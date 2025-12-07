import os
import sys
import time
import base64
import requests
import re
import threading
import wave
import flet as ft
import google.generativeai as genai
import glob
import concurrent.futures
import logging
import traceback
import shutil
import warnings
import json

# ==========================================
# 0. 初始化與設定
# ==========================================
warnings.filterwarnings("ignore", category=DeprecationWarning)

# 設定日誌
logger = logging.getLogger()
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler = logging.FileHandler('app.log', encoding='utf-8')
file_handler.setFormatter(formatter)
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(formatter)
if logger.hasHandlers(): logger.handlers.clear()
logger.addHandler(file_handler)
logger.addHandler(stream_handler)

APP_TITLE = "阿嬤的讀信機"
APP_VERSION = "v3.0 (Pro Player)"

# 1秒鐘的靜音 WAV (Base64)
SILENT_WAV_B64 = "UklGRiYAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA="

# ==========================================
# 1. 設定與 Key 載入
# ==========================================
def load_key(env_name, filename):
    env_key = os.environ.get(env_name)
    if env_key: return env_key.strip()
    try:
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f: return f.read().strip()
    except: pass
    return None

def load_file_content(filename, default_content):
    try:
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f: return f.read().strip()
    except: pass
    return default_content

def load_json_config(filename, default_config):
    try:
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                config = json.load(f)
                return {**default_config, **config}
    except: pass
    return default_config

GEMINI_API_KEY = load_key("GEMINI_API_KEY", "Gemini_API.txt")
YATING_API_KEY = load_key("YATING_API_KEY", "Yating_API.txt")

# ★★★ Prompt 極簡化修正：嚴格禁止廢話 ★★★
DEFAULT_PROMPT_SIMPLE = """
任務：看完這張圖片，用「最簡短的台語口語」講重點。
規則：
1. 直接講結果，禁止說「這張圖是...」、「重點是...」這種開場白。
2. 收據只唸總金額；藥單只唸吃法。
3. 50字以內。
"""

DEFAULT_PROMPT_DETAILED = """
任務：你是一個OCR讀稿機。將圖片文字轉成台語漢字朗讀。
嚴格規則：
1. **絕對禁止**加開場白（如：以下是內容、這張圖寫著...）。
2. **絕對禁止**解釋含義。
3. **直接開始唸**圖片上的第一個字。
4. 遇到無意義的亂碼或Logo請跳過。
"""

PROMPT_SIMPLE = load_file_content("prompt_simple.txt", DEFAULT_PROMPT_SIMPLE)
PROMPT_DETAILED = load_file_content("prompt_detailed.txt", DEFAULT_PROMPT_DETAILED)

DEFAULT_UI_CONFIG = {
    "app_bgcolor": "#FFF8E1",
    "text_color_primary": "#5D4037",
    "text_color_secondary": "#8D6E63",
    "btn_simple_color": "#2196F3",
    "btn_detailed_color": "#F44336",
    "btn_play_bg_color": "white",
    "btn_play_text_color": "#4CAF50",
    "status_icon_idle": "#FF9800",
    "status_icon_thinking": "#2196F3",
    "status_icon_speaking": "#4CAF50",
    "status_icon_error": "red"
}
UI_CONFIG = load_json_config("ui_config.json", DEFAULT_UI_CONFIG)

# ==========================================
# 2. 大腦模組：Gemini
# ==========================================
def ask_gemini_intent(image_bytes, is_detailed=False):
    logging.info("呼叫 Gemini...")
    if not GEMINI_API_KEY: raise ValueError("找不到 Gemini API Key")
    genai.configure(api_key=GEMINI_API_KEY)

    prompt = PROMPT_DETAILED if is_detailed else PROMPT_SIMPLE

    candidate_models = []
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                candidate_models.append(m.name)
    except: pass

    if not candidate_models:
        candidate_models = ['models/gemini-1.5-flash', 'models/gemini-pro']

    def model_priority(name):
        if 'flash' in name.lower(): return 0
        return 1
    candidate_models.sort(key=model_priority)
    
    last_error = None
    for model_name in candidate_models:
        try:
            logging.info(f"嘗試模型: {model_name}")
            model = genai.GenerativeModel(model_name)
            response = model.generate_content([prompt, {'mime_type': 'image/jpeg', 'data': image_bytes}])
            if response.text: return response.text
        except Exception as e:
            logging.warning(f"模型 {model_name} 失敗: {e}")
            last_error = e
            continue
            
    raise RuntimeError(f"AI 讀取失敗: {str(last_error)}")

# ==========================================
# 3. 嘴巴模組：雅婷 TTS
# ==========================================
def split_text_smartly(text, limit=280):
    sentences = re.split(r'(。|，|\n|；|！|？)', text)
    chunks, current = [], ""
    for i in range(0, len(sentences)-1, 2):
        s = sentences[i] + sentences[i+1]
        if len(current) + len(s) < limit: current += s
        else:
            if current: chunks.append(current)
            current = s 
    if len(sentences) % 2 != 0: current += sentences[-1]
    if current: chunks.append(current)
    return chunks

def download_chunk_safe(params):
    text_chunk, index = params
    if not YATING_API_KEY: raise ValueError("缺少 Yating API Key")
    
    for attempt in range(3):
        try:
            response = requests.post(
                "https://tts.api.yating.tw/v2/speeches/short",
                headers={"Content-Type": "application/json", "key": YATING_API_KEY},
                json={
                    "input": {"text": text_chunk, "type": "text"},
                    "voice": {"model": "tai_female_1", "speed": 1.0, "pitch": 1.0, "energy": 1.0},
                    "audioConfig": {"encoding": "LINEAR16", "sampleRate": "16K"}
                },
                timeout=30
            )
            if response.status_code in [200, 201]:
                data = response.json()
                if data.get("audioContent"):
                    temp_name = f"temp_part_{index}.wav"
                    with open(temp_name, "wb") as f: 
                        f.write(base64.b64decode(data.get("audioContent")))
                    return (index, temp_name)
            time.sleep(1)
        except: time.sleep(1)
    return None

def generate_merged_audio(text):
    chunks = split_text_smartly(text)
    temp_files_map = {}
    created_files = []
    
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            tasks = [(chunk, i) for i, chunk in enumerate(chunks)]
            futures = {executor.submit(download_chunk_safe, task): task[1] for task in tasks}
            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                if res:
                    idx, fname = res
                    temp_files_map[idx] = fname
                    created_files.append(fname)

        if len(temp_files_map) != len(chunks): raise RuntimeError("語音合成不完整")
        
        filename = f"audio_{int(time.time())}.wav"
        output_filepath = os.path.join("assets", filename)
        sorted_files = [temp_files_map[i] for i in range(len(chunks))]
        
        with wave.open(output_filepath, 'wb') as wav_out:
            for i, temp_file in enumerate(sorted_files):
                with wave.open(temp_file, 'rb') as wav_in:
                    if i == 0: wav_out.setparams(wav_in.getparams())
                    wav_out.writeframes(wav_in.readframes(wav_in.getnframes()))
                try: os.remove(temp_file)
                except: pass
        return filename
    except Exception as e:
        for f in created_files:
            if os.path.exists(f): os.remove(f)
        raise e

# ==========================================
# 4. App 主介面 (專業播放器版)
# ==========================================
def main(page: ft.Page):
    page.title = APP_TITLE
    page.bgcolor = UI_CONFIG["app_bgcolor"]
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 20
    page.upload_dir = "uploads"
    os.makedirs("uploads", exist_ok=True)
    os.makedirs("assets", exist_ok=True)

    # Audio 元件 (核心)
    audio_player = ft.Audio(src_base64=SILENT_WAV_B64, autoplay=False)
    page.overlay.append(audio_player)

    # 狀態變數
    current_mode = {"is_detailed": False}
    is_seeking = False 
    
    # --- 元件定義 ---

    # 1. 標題列
    title_text = ft.Text("👵 阿嬤的讀信機", size=24, weight="bold", color=UI_CONFIG["text_color_primary"])
    
    def toggle_debug(e):
        is_debug = result_text_box.visible
        result_text_box.visible = not is_debug
        status_container.height = 150 if is_debug else 50
        status_icon.size = 120 if is_debug else 40
        btn_debug.icon = "visibility" if is_debug else "visibility_off"
        page.update()

    btn_debug = ft.IconButton(icon="visibility", icon_color="grey", tooltip="顯示文字", on_click=toggle_debug)
    header = ft.Row([title_text, ft.Container(width=10), btn_debug], alignment=ft.MainAxisAlignment.CENTER)

    # 2. 中間區 (圖示 + 文字)
    status_icon = ft.Icon(name="camera_alt_rounded", size=120, color=UI_CONFIG["status_icon_idle"])
    status_spinner = ft.ProgressRing(width=80, height=80, stroke_width=8, color="#2196F3", visible=False)
    
    status_container = ft.Container(
        content=ft.Stack([
            ft.Container(content=status_icon, alignment=ft.alignment.center),
            ft.Container(content=status_spinner, alignment=ft.alignment.center),
        ]),
        height=150, alignment=ft.alignment.center
    )

    status_label = ft.Text("請選擇模式\n開始拍照", size=24, weight="bold", color=UI_CONFIG["text_color_primary"], text_align=ft.TextAlign.CENTER)

    # 文字顯示區
    result_text = ft.Text("", size=16, color="black", selectable=True)
    result_text_box = ft.Container(
        content=ft.Column([
            ft.Text("【辨識結果】", size=14, color="blue"),
            ft.Column([result_text], scroll=ft.ScrollMode.AUTO, expand=True)
        ], expand=True),
        bgcolor="white", padding=10, border_radius=10, border=ft.border.all(1, "grey"),
        visible=False, expand=True
    )

    center_column = ft.Column(
        [status_container, status_label, result_text_box],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=10, expand=True
    )

    # 3. ★★★ 全新設計：音訊播放控制器 (Audio Player Bar) ★★★
    
    # 時間顯示 (00:00 / 00:00)
    txt_time = ft.Text("00:00 / 00:00", size=14, color=UI_CONFIG["text_color_secondary"], weight="bold")
    
    # 播放/暫停按鈕
    btn_play_pause = ft.IconButton(
        icon="play_circle_fill", 
        icon_size=40, 
        icon_color=UI_CONFIG["status_icon_speaking"],
        on_click=lambda e: cmd_play_pause()
    )

    # 進度條
    slider_progress = ft.Slider(
        min=0, max=1000, value=0, 
        expand=True, 
        active_color=UI_CONFIG["status_icon_speaking"],
        inactive_color="#E0E0E0",
        thumb_color="green",
    )

    # 播放器容器 (預設隱藏)
    player_bar = ft.Container(
        content=ft.Row([
            btn_play_pause,
            slider_progress,
            txt_time
        ], alignment=ft.MainAxisAlignment.CENTER),
        bgcolor="white",
        padding=10,
        border_radius=50,
        shadow=ft.BoxShadow(spread_radius=1, blur_radius=5, color="#20000000", offset=ft.Offset(0, 2)),
        visible=False,
        margin=ft.margin.only(bottom=15)
    )

    # 4. 底部按鈕區
    def make_big_button(icon_name, text, color, on_click):
        return ft.Container(
            content=ft.Row([ft.Icon(icon_name, size=28, color="white"), ft.Text(text, size=20, weight="bold", color="white")], alignment=ft.MainAxisAlignment.CENTER),
            bgcolor=color, padding=15, border_radius=50,
            shadow=ft.BoxShadow(spread_radius=1, blur_radius=10, color="#4D000000", offset=ft.Offset(0, 5)),
            on_click=on_click, ink=True, expand=True
        )

    btn_simple = make_big_button("short_text", "簡略模式", UI_CONFIG["btn_simple_color"], lambda e: call_upload(False))
    btn_detailed = make_big_button("description", "照片模式", UI_CONFIG["btn_detailed_color"], lambda e: call_upload(True))
    buttons_row = ft.Row([btn_simple, ft.Container(width=10), btn_detailed], alignment=ft.MainAxisAlignment.CENTER)

    footer = ft.Container(
        content=ft.Column([player_bar, buttons_row]),
        padding=ft.margin.only(bottom=10)
    )

    # --- 播放器邏輯 ---
    
    def cmd_play_pause():
        # 切換播放/暫停
        if btn_play_pause.icon == "pause_circle_filled":
            audio_player.pause()
            btn_play_pause.icon = "play_circle_fill"
        else:
            audio_player.resume()
            btn_play_pause.icon = "pause_circle_filled"
        page.update()

    def seek_start(e):
        nonlocal is_seeking
        is_seeking = True

    def seek_end(e):
        nonlocal is_seeking
        pos_ms = int(slider_progress.value)
        audio_player.seek(pos_ms)
        is_seeking = False
        # 拖曳結束後自動播放
        audio_player.resume()
        btn_play_pause.icon = "pause_circle_filled"
        page.update()

    slider_progress.on_change_start = seek_start
    slider_progress.on_change_end = seek_end

    def on_position_changed(e):
        if not is_seeking:
            pos = float(e.data)
            dur = audio_player.get_duration()
            if dur and dur > 0:
                slider_progress.max = dur
                slider_progress.value = min(pos, dur)
                # 格式化時間 mm:ss
                p_m, p_s = divmod(int(pos/1000), 60)
                d_m, d_s = divmod(int(dur/1000), 60)
                txt_time.value = f"{p_m:02}:{p_s:02} / {d_m:02}:{d_s:02}"
                page.update()

    def on_player_state_changed(e):
        if e.data == "completed":
            btn_play_pause.icon = "play_circle_fill" # 播完變回播放鍵
            slider_progress.value = 0
            page.update()

    audio_player.on_position_changed = on_position_changed
    audio_player.on_state_changed = on_player_state_changed

    # --- 核心流程 ---

    def update_status(mode):
        if mode == "idle":
            status_icon.name = "camera_alt_rounded"
            status_icon.color = UI_CONFIG["status_icon_idle"]
            status_icon.visible = True
            status_spinner.visible = False
            status_label.value = "請選擇模式\n開始拍照"
            buttons_row.visible = True
            player_bar.visible = False # 閒置時隱藏播放器
        elif mode == "uploading":
            status_icon.visible = False
            status_spinner.visible = True
            status_label.value = "相片上傳中..."
            buttons_row.visible = False 
            player_bar.visible = False
        elif mode == "thinking":
            status_icon.name = "psychology"
            status_icon.color = UI_CONFIG["status_icon_thinking"]
            status_icon.visible = True
            status_spinner.visible = True
            status_label.value = "阿嬤修等幾勒\n我咧看信..."
        elif mode == "speaking":
            status_icon.name = "record_voice_over"
            status_icon.color = UI_CONFIG["status_icon_speaking"]
            status_icon.visible = True
            status_spinner.visible = False
            status_label.value = "讀完囉！"
            buttons_row.visible = True
            player_bar.visible = True # 顯示播放器
            btn_play_pause.icon = "pause_circle_filled" # 預設顯示暫停(代表正在播)
        elif mode == "error":
            status_icon.name = "error_outline"
            status_icon.color = UI_CONFIG["status_icon_error"]
            status_icon.visible = True
            status_spinner.visible = False
            buttons_row.visible = True
        page.update()

    def run_ai_task(image_bytes):
        try:
            update_status("thinking")
            text = ask_gemini_intent(image_bytes, current_mode["is_detailed"])
            result_text.value = text
            page.update()
            
            wav_file = generate_merged_audio(text)
            
            update_status("speaking")
            
            # 設定音源 (確保每次都是新的 src，解決紅畫面)
            audio_player.src = wav_file
            audio_player.update()
            
            time.sleep(0.5)
            audio_player.play()
        except Exception as e:
            update_status("error")
            status_label.value = "拍謝，看無！\n請再拍一次"
            result_text.value = f"錯誤詳情: {e}"
            page.update()

    def on_upload_result(e: ft.FilePickerUploadEvent):
        if e.progress == 1.0:
            file_path = os.path.join(page.upload_dir, e.file_name)
            try:
                with open(file_path, "rb") as f: image_bytes = f.read()
                threading.Thread(target=run_ai_task, args=(image_bytes,), daemon=True).start()
            except Exception as err:
                update_status("error")
                status_label.value = "讀取失敗"
                page.update()

    def call_upload(is_detailed):
        current_mode["is_detailed"] = is_detailed
        result_text.value = ""
        # 隱藏舊的播放器，避免誤觸
        player_bar.visible = False 
        file_picker.pick_files(allow_multiple=False, file_type=ft.FilePickerFileType.IMAGE)

    def on_file_picked(e: ft.FilePickerResultEvent):
        if e.files:
            update_status("uploading")
            file_obj = e.files[0]
            upload_url = page.get_upload_url(file_obj.name, 600)
            file_picker.upload([ft.FilePickerUploadFile(file_obj.name, upload_url)])
        else:
            update_status("idle")

    file_picker = ft.FilePicker(on_result=on_file_picked, on_upload=on_upload_result)
    page.overlay.append(file_picker)

    page.add(ft.Column([header, center_column, footer], expand=True, alignment=ft.MainAxisAlignment.SPACE_BETWEEN))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 0))
    os.makedirs("uploads", exist_ok=True)
    os.makedirs("assets", exist_ok=True)
    os.environ["FLET_SECRET_KEY"] = "GrandmaSecret2025"
    try:
        print("🚀 啟動中...")
        ft.app(target=main, view=ft.AppView.WEB_BROWSER, port=port, upload_dir="uploads", assets_dir="assets")
    except:
        ft.app(target=main, view=ft.AppView.WEB_BROWSER, port=port, upload_dir="uploads", assets_dir="assets")
