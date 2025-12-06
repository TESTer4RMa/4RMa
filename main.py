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

# 1秒鐘的靜音 WAV (Base64)，用來騙過瀏覽器和 Flet 的初始化檢查，防止紅畫面
SILENT_WAV_B64 = "UklGRiYAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA="

# ==========================================
# 1. API Key 載入
# ==========================================
def load_key(env_name, filename):
    env_key = os.environ.get(env_name)
    if env_key: return env_key.strip()
    try:
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f: return f.read().strip()
    except: pass
    return None

GEMINI_API_KEY = load_key("GEMINI_API_KEY", "Gemini_API.txt")
YATING_API_KEY = load_key("YATING_API_KEY", "Yating_API.txt")

# ==========================================
# 2. 大腦模組：Gemini
# ==========================================
def ask_gemini_intent(image_bytes, is_detailed=False):
    logging.info("呼叫 Gemini...")
    if not GEMINI_API_KEY: raise ValueError("找不到 Gemini API Key")
    genai.configure(api_key=GEMINI_API_KEY)

    # 針對長輩優化的 Prompt
    prompt = """
    你是一位會講台語的貼心秘書。請看這張圖片，用「最簡單、最白話」的台語口語唸給阿嬤聽。
    
    **規則：**
    1. **只講重點**：如果是藥袋，只講什麼藥、怎麼吃；如果是帳單，只講多少錢、截止日。
    2. **語氣親切**：開頭可以加「阿嬤，這張是...」。
    3. **全台語漢字**：請將內容轉寫為台語漢字（教育部推薦用字）。
    4. **字數限制**：請控制在 80 字以內，阿嬤沒耐心聽太久。
    """
    if is_detailed:
        prompt = "請將圖片內容「逐字」轉成台語口語唸出來，不要遺漏細節。"

    # 自動偵測模型
    candidate_models = ['models/gemini-1.5-flash', 'models/gemini-pro', 'models/gemini-1.5-flash-latest']
    
    last_error = None
    for model_name in candidate_models:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content([prompt, {'mime_type': 'image/jpeg', 'data': image_bytes}])
            if response.text: return response.text
        except Exception as e:
            last_error = e
            continue
            
    raise RuntimeError(f"AI 讀取失敗，請重試: {str(last_error)}")

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
    
    for attempt in range(3): # 重試 3 次
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
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
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
# 4. App 主介面 (阿嬤友善版)
# ==========================================
def main(page: ft.Page):
    # 基礎設定
    page.title = APP_TITLE
    page.bgcolor = "#FFF8E1" # 溫暖的米黃色背景
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 20
    page.upload_dir = "uploads"
    os.makedirs("uploads", exist_ok=True)
    os.makedirs("assets", exist_ok=True)

    # 解決紅畫面關鍵：初始化給它一個 Base64 的靜音檔
    audio_player = ft.Audio(src_base64=SILENT_WAV_B64, autoplay=False)
    page.overlay.append(audio_player)

    # --- UI 狀態控制變數 ---
    current_mode = {"is_detailed": False}
    
    # --- 元件定義 ---
    
    # 1. 頂部標題
    header = ft.Container(
        content=ft.Column([
            ft.Text("👵 阿嬤的讀信機", size=32, weight="bold", color="#5D4037"),
            ft.Text("拍藥單、讀信、唸簡訊", size=18, color="#8D6E63"),
        ], spacing=5, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        alignment=ft.alignment.center,
        margin=ft.margin.only(bottom=20)
    )

    # 2. 中央大圖示 (狀態顯示區)
    # 改用字串名稱，避免 AttributeError
    status_icon = ft.Icon(name="camera_alt_rounded", size=120, color="#FF9800")
    status_spinner = ft.ProgressRing(width=80, height=80, stroke_width=8, color="#2196F3", visible=False)
    
    # 狀態文字 (超大字體)
    status_label = ft.Text("按下面按鈕\n開始拍照", size=28, weight="bold", color="#4E342E", text_align=ft.TextAlign.CENTER)
    
    # 顯示辨識結果的區域 (預設隱藏)
    result_card = ft.Container(
        content=ft.Column([
            ft.Text("阿嬤，這張寫的是：", size=20, color="blue"),
            ft.Text("", size=24, weight="bold", color="black", ref=None), # 這裡會填入結果
        ]),
        bgcolor="white",
        padding=20,
        border_radius=15,
        visible=False,
        border=ft.border.all(2, "#E0E0E0")
    )
    result_text_ref = result_card.content.controls[1] # 取得文字元件的引用

    # 中央顯示區塊
    center_display = ft.Container(
        content=ft.Column([
            ft.Container(height=20),
            ft.Stack([
                ft.Container(content=status_icon, alignment=ft.alignment.center),
                ft.Container(content=status_spinner, alignment=ft.alignment.center),
            ], height=150),
            ft.Container(height=20),
            status_label,
            ft.Container(height=20),
            result_card
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        alignment=ft.alignment.center,
        expand=True
    )

    # 3. 底部大按鈕 (操作區)
    # 用 Container 做按鈕，因為可以做得更大更漂亮
    def make_big_button(icon_name, text, color, on_click):
        return ft.Container(
            content=ft.Row([
                ft.Icon(icon_name, size=40, color="white"),
                ft.Text(text, size=28, weight="bold", color="white"),
            ], alignment=ft.MainAxisAlignment.CENTER, spacing=10),
            bgcolor=color,
            padding=20,
            border_radius=50, # 圓角
            # 使用透明黑色的 Hex 代碼 #4D000000 (約30%透明度) 取代 ft.colors.with_opacity
            shadow=ft.BoxShadow(spread_radius=1, blur_radius=10, color="#4D000000", offset=ft.Offset(0, 5)),
            on_click=on_click,
            ink=True, # 點擊波紋效果
        )

    # 改用字串名稱
    btn_camera = make_big_button("camera_alt", " 影 相 片 ", "#FF9800", lambda e: call_upload())
    
    # 播放按鈕 (預設隱藏)
    btn_play = ft.Container(
        content=ft.Row([
            # 改用字串名稱
            ft.Icon("play_circle_fill", size=50, color="white"),
            ft.Text(" 再聽一次 ", size=28, weight="bold", color="white"),
        ], alignment=ft.MainAxisAlignment.CENTER),
        bgcolor="#4CAF50",
        padding=20,
        border_radius=50,
        # 使用透明黑色的 Hex 代碼 #4D000000 取代 ft.colors.with_opacity
        shadow=ft.BoxShadow(spread_radius=1, blur_radius=10, color="#4D000000", offset=ft.Offset(0, 5)),
        on_click=lambda e: audio_player.play(),
        visible=False,
        ink=True
    )

    # 底部容器
    footer = ft.Container(
        content=ft.Column([
            btn_play,
            ft.Container(height=10),
            btn_camera
        ]),
        padding=ft.margin.only(bottom=30)
    )

    # --- 邏輯處理區 ---

    def update_status(mode):
        """切換介面狀態 (Idle, Loading, Playing, Error)"""
        # 使用字串名稱設定 Icon，避免 Attribute Error
        if mode == "idle":
            status_icon.name = "camera_alt_rounded"
            status_icon.color = "#FF9800" # 橘色
            status_icon.visible = True
            status_spinner.visible = False
            status_label.value = "按下面按鈕\n開始拍照"
            btn_camera.disabled = False
            btn_play.visible = False
            
        elif mode == "uploading":
            status_icon.visible = False
            status_spinner.visible = True
            status_label.value = "相片上傳中..."
            btn_camera.disabled = True
            
        elif mode == "thinking":
            status_icon.name = "psychology"
            status_icon.color = "#2196F3" # 藍色
            status_icon.visible = True
            status_spinner.visible = True # 繼續轉
            status_label.value = "阿嬤修等幾勒\n我咧看信..." # 台語親切感
            
        elif mode == "speaking":
            status_icon.name = "record_voice_over"
            status_icon.color = "#4CAF50" # 綠色
            status_icon.visible = True
            status_spinner.visible = False
            status_label.value = "讀完囉！\n沒聽到請按綠色按鈕"
            btn_camera.disabled = False
            btn_play.visible = True
            
        elif mode == "error":
            status_icon.name = "error_outline"
            status_icon.color = "red"
            status_icon.visible = True
            status_spinner.visible = False
            # 錯誤訊息會在外面設定
            btn_camera.disabled = False

        page.update()

    def run_ai_task(image_bytes):
        try:
            update_status("thinking")
            
            # 1. 辨識
            text = ask_gemini_intent(image_bytes, current_mode["is_detailed"])
            result_text_ref.value = text
            result_card.visible = True
            page.update()
            
            # 2. 合成
            wav_file = generate_merged_audio(text)
            
            # 3. 準備播放
            update_status("speaking")
            audio_player.src = wav_file
            audio_player.update()
            
            # 嘗試自動播放 (手機可能會擋，所以我們有顯示綠色大按鈕)
            time.sleep(0.5)
            audio_player.play()
            
        except Exception as e:
            update_status("error")
            status_label.value = "拍謝，剛才沒看清楚\n請再拍一次"
            logging.error(f"Error: {e}")
            page.update()

    def on_upload_result(e: ft.FilePickerUploadEvent):
        if e.progress == 1.0:
            file_path = os.path.join(page.upload_dir, e.file_name)
            try:
                with open(file_path, "rb") as f: image_bytes = f.read()
                # 啟動後台執行緒
                threading.Thread(target=run_ai_task, args=(image_bytes,), daemon=True).start()
            except Exception as err:
                update_status("error")
                status_label.value = "讀取檔案失敗"
                page.update()

    def call_upload():
        # 呼叫上傳前，先清空舊狀態
        result_card.visible = False
        btn_play.visible = False
        file_picker.pick_files(allow_multiple=False, file_type=ft.FilePickerFileType.IMAGE)

    def on_file_picked(e: ft.FilePickerResultEvent):
        if e.files:
            update_status("uploading")
            file_obj = e.files[0]
            upload_url = page.get_upload_url(file_obj.name, 600)
            file_picker.upload([ft.FilePickerUploadFile(file_obj.name, upload_url)])
        else:
            # 使用者取消，回到原狀
            update_status("idle")

    # 初始化 FilePicker
    file_picker = ft.FilePicker(on_result=on_file_picked, on_upload=on_upload_result)
    page.overlay.append(file_picker)

    # 組合畫面
    page.add(
        ft.Column([
            header,
            center_display,
            footer
        ], expand=True, alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 0))
    # 建立目錄
    os.makedirs("uploads", exist_ok=True)
    os.makedirs("assets", exist_ok=True)
    # 設定密鑰
    os.environ["FLET_SECRET_KEY"] = "GrandmaSecret2025"
    
    try:
        print("🚀 啟動中...")
        ft.app(target=main, view=ft.AppView.WEB_BROWSER, port=port, host="0.0.0.0", upload_dir="uploads", assets_dir="assets")
    except:
        ft.app(target=main, view=ft.AppView.WEB_BROWSER, port=port, upload_dir="uploads", assets_dir="assets")
