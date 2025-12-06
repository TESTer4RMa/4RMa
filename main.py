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
import warnings # 加入這個來過濾警告

# ==========================================
# 0. 初始化日誌與設定 (Debug 增強版)
# ==========================================
# 忽略 Flet 的 DeprecationWarning (Audio)
warnings.filterwarnings("ignore", category=DeprecationWarning)

# 建立 Logger
logger = logging.getLogger()
logger.setLevel(logging.INFO) # 改成 INFO，這樣連普通訊息都看得到

# 格式設定
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# 1. 輸出到檔案 (app.log)
file_handler = logging.FileHandler('app.log', encoding='utf-8')
file_handler.setFormatter(formatter)

# 2. 同時輸出到螢幕 (Console)
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(formatter)

# 清除舊的 handler 避免重複，然後加入新的
if logger.hasHandlers():
    logger.handlers.clear()
logger.addHandler(file_handler)
logger.addHandler(stream_handler)

APP_TITLE = "阿嬤的讀信機 (全平台通用版)"

# ==========================================
# 1. API Key 載入 (安全雙軌制)
# ==========================================
def load_key(env_name, filename):
    """
    載入 API Key 的策略：
    1. 優先嘗試讀取「系統環境變數」(os.environ) -> 適合雲端部署，Key 不會外洩。
    2. 如果沒有，再嘗試讀取「本地文字檔」(.txt) -> 適合本機測試，方便直接修改。
    """
    # 1. 嘗試環境變數 (雲端優先)
    env_key = os.environ.get(env_name)
    if env_key:
        logging.info(f"✅ 成功從環境變數載入: {env_name}")
        return env_key.strip()

    # 2. 嘗試本地檔案 (本機備用)
    try:
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f: 
                file_key = f.read().strip()
                logging.info(f"📂 成功從檔案載入: {filename}")
                return file_key
    except Exception as e: 
        logging.error(f"讀取 Key 檔案失敗: {e}")
        
    logging.warning(f"⚠️ 找不到 Key: {env_name} 或 {filename}")
    return None

# 修改這裡：同時指定「環境變數名稱」與「檔案名稱」
GEMINI_API_KEY = load_key("GEMINI_API_KEY", "Gemini_API.txt")
YATING_API_KEY = load_key("YATING_API_KEY", "Yating_API.txt")

# ==========================================
# 2. 大腦模組：Gemini (智慧自動偵測)
# ==========================================
def ask_gemini_intent(image_bytes, is_detailed=False):
    logging.info("正在呼叫 Gemini AI...")
    if not GEMINI_API_KEY: raise ValueError("找不到 Gemini API Key")

    # 設定 API Key
    genai.configure(api_key=GEMINI_API_KEY)

    # ★★★ 關鍵修正：不再用猜的，直接問 Google 有哪些模型可用 ★★★
    candidate_models = []
    try:
        logging.info("正在查詢您的 API Key 可用的模型清單...")
        for m in genai.list_models():
            # 只找支援 'generateContent' 的模型
            if 'generateContent' in m.supported_generation_methods:
                candidate_models.append(m.name)
        
        logging.info(f"Google 回傳可用模型: {candidate_models}")
    except Exception as e:
        logging.warning(f"無法列出模型 (將使用預設清單嘗試): {e}")

    # 如果自動偵測失敗，才使用備用清單
    if not candidate_models:
        candidate_models = [
            'models/gemini-1.5-flash',
            'models/gemini-1.5-flash-latest',
            'models/gemini-pro',
            'models/gemini-pro-vision',
            'models/gemini-1.0-pro'
        ]

    # ★ 排序策略：優先使用 flash (速度快/便宜) > pro (穩定) > exp (實驗版易失敗)
    def model_priority(name):
        name = name.lower()
        if 'flash' in name and 'exp' not in name: return 0  # 最優先：穩定版 flash
        if 'pro' in name and 'exp' not in name: return 1    # 次優先：穩定版 pro
        if 'flash' in name: return 2                        # 再次：實驗版 flash
        return 3                                            # 最後：其他

    # 重新排序候選名單
    candidate_models.sort(key=model_priority)
    logging.info(f"嘗試順序: {candidate_models}")

    last_error = None
    response = None

    for model_name in candidate_models:
        try:
            logging.info(f"正在嘗試模型: {model_name}")
            model = genai.GenerativeModel(model_name)
            
            if is_detailed:
                prompt = "你現在是一個「台語讀稿機」。請將圖片上的文字，**逐字逐句**轉換成台語口語唸出來。要求：忠實還原、直讀、台語化。只輸出台語漢字。"
            else:
                prompt = "你是一位貼心的秘書。請看這張圖片，幫阿嬤判斷「核心重點」是什麼。要求：只講結論(藥單唸藥名吃法、帳單唸金額)、100字內。只輸出台語漢字。"
            
            logging.info(f"發送圖片大小: {len(image_bytes)} bytes")
            # 嘗試生成內容
            response = model.generate_content([prompt, {'mime_type': 'image/jpeg', 'data': image_bytes}])
            
            # 如果成功執行到這裡，代表模型可用，跳出迴圈
            logging.info(f"✅ 模型 {model_name} 執行成功！")
            break 

        except Exception as e:
            # 印出錯誤並繼續下一個
            logging.warning(f"❌ 模型 {model_name} 失敗: {e}")
            last_error = e
            continue
    
    # 檢查有沒有任何一個成功
    if response and response.text:
        logging.info(f"Gemini 回應: {response.text[:50]}...") 
        return response.text
    else:
        # 如果全部都失敗，拋出最後一個錯誤
        logging.error("😱 所有模型嘗試皆失敗，請檢查 API Key 是否有權限或額度")
        raise RuntimeError(f"AI 識別失敗 (已嘗試所有可用模型): {str(last_error)}")

# ==========================================
# 3. 嘴巴模組：雅婷 TTS (強化版)
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
    
    # ★★★ 關鍵修正：增加重試機制 (Retry) ★★★
    max_retries = 3
    timeout_sec = 30 # 延長 timeout 到 30 秒

    for attempt in range(max_retries):
        try:
            logging.info(f"正在下載語音片段 {index} (嘗試 {attempt + 1}/{max_retries})...")
            response = requests.post(
                "https://tts.api.yating.tw/v2/speeches/short",
                headers={"Content-Type": "application/json", "key": YATING_API_KEY},
                json={
                    "input": {"text": text_chunk, "type": "text"},
                    "voice": {"model": "tai_female_1", "speed": 1.0, "pitch": 1.0, "energy": 1.0},
                    "audioConfig": {"encoding": "LINEAR16", "sampleRate": "16K"}
                },
                timeout=timeout_sec
            )
            response.raise_for_status()
            data = response.json()
            if data.get("audioContent"):
                temp_name = f"temp_part_{index}.wav"
                with open(temp_name, "wb") as f: 
                    f.write(base64.b64decode(data.get("audioContent")))
                return (index, temp_name)
        except Exception as e:
            logging.warning(f"Chunk {index} 下載失敗 (嘗試 {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2) # 休息 2 秒再試
            else:
                logging.error(f"Chunk {index} 最終失敗")
                raise e # 試了 3 次都失敗，往上拋出錯誤
    return None

def generate_merged_audio(text):
    chunks = split_text_smartly(text)
    temp_files_map = {}
    created_files = []
    
    try:
        # ★★★ 關鍵修正：降低平行下載數 (3 -> 2)，避免網路塞車導致 Timeout ★★★
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            tasks = [(chunk, i) for i, chunk in enumerate(chunks)]
            futures = {executor.submit(download_chunk_safe, task): task[1] for task in tasks}
            for future in concurrent.futures.as_completed(futures):
                try:
                    idx, fname = future.result()
                    temp_files_map[idx] = fname
                    created_files.append(fname)
                except Exception as e: raise e

        if len(temp_files_map) != len(chunks): raise RuntimeError("下載片段不全")
        
        # ★★★ 修改這裡：把檔案存到 assets 資料夾底下 (解決手機播放問題) ★★★
        filename = f"full_audio_{int(time.time())}.wav"
        # 確保路徑是 assets/filename.wav
        output_filepath = os.path.join("assets", filename)
        
        sorted_files = [temp_files_map[i] for i in range(len(chunks))]
        
        with wave.open(output_filepath, 'wb') as wav_out:
            for i, temp_file in enumerate(sorted_files):
                with wave.open(temp_file, 'rb') as wav_in:
                    if i == 0: wav_out.setparams(wav_in.getparams())
                    wav_out.writeframes(wav_in.readframes(wav_in.getnframes()))
                try: os.remove(temp_file)
                except: pass
        
        logging.info(f"語音合併完成: {output_filepath}")
        return filename # ★★★ 注意：Flet 只需要檔名，它會自動去 assets 資料夾找
    except Exception as e:
        for f in created_files:
            if os.path.exists(f): os.remove(f)
        raise e

# ==========================================
# 4. App 主介面 (支援上傳模式)
# ==========================================
def main(page: ft.Page):
    page.title = APP_TITLE
    page.window_width = 480
    page.window_height = 850
    page.bgcolor = "#FFF8F0"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.upload_dir = "uploads"
    # 確保資料夾存在
    os.makedirs(page.upload_dir, exist_ok=True)
    os.makedirs("assets", exist_ok=True) # 確保 assets 也存在

    is_seeking = False 
    current_mode = {"is_detailed": False}
    # 緩存音訊長度，避免重複查詢造成 Timeout
    current_duration = 0 

    img_display = ft.Image(src="", width=300, height=300, fit=ft.ImageFit.CONTAIN, visible=False)
    result_text = ft.Text(value="請選一張相片...", size=20, color="#333", weight="bold")
    status_text = ft.Text(value="準備就緒", size=14, color="grey")
    
    slider_progress = ft.Slider(min=0, max=1000, value=0, expand=True, disabled=True)
    txt_duration = ft.Text("00:00 / 00:00", size=12, color="grey")
    btn_play = ft.IconButton(icon="play_circle", icon_size=60, icon_color="blue", disabled=True)
    
    panel_player = ft.Column([
        ft.Row([slider_progress]),
        ft.Row([txt_duration, ft.Container(expand=True)]),
        ft.Row([btn_play], alignment=ft.MainAxisAlignment.CENTER)
    ], visible=False)

    scroll_container = ft.Column([result_text, status_text], scroll=ft.ScrollMode.AUTO, height=150)
    audio_player = ft.Audio(src="", autoplay=False)
    page.overlay.append(audio_player)

    def show_error(msg):
        logging.error(f"UI顯示錯誤: {msg}")
        status_text.value = f"❌ {msg}"
        status_text.color = "red"
        page.update()

    def cleanup():
        # 清理舊的音檔，包括 assets 裡面的
        try:
            for f in glob.glob("assets/full_audio_*.wav") + glob.glob("full_audio_*.wav") + glob.glob("temp_part_*.wav"):
                try: os.remove(f)
                except: pass
        except: pass

    def run_process_in_thread(image_bytes, is_detailed):
        logging.info("執行緒啟動: 開始處理圖片")
        try:
            if not GEMINI_API_KEY or not YATING_API_KEY: raise ValueError("缺少 API Key")
            
            taigi_reply = ask_gemini_intent(image_bytes, is_detailed)
            result_text.value = taigi_reply
            status_text.value = "AI 思考完畢，正在合成語音..."
            status_text.color = "#1976D2"
            page.update()

            final_wav_filename = generate_merged_audio(taigi_reply)
            
            status_text.value = "準備播放..."
            status_text.color = "green"
            audio_player.src = final_wav_filename
            audio_player.update()
            
            # 重置播放狀態
            nonlocal current_duration
            current_duration = 0
            
            panel_player.visible = True
            btn_play.disabled = False
            btn_play.icon = "pause_circle"
            slider_progress.disabled = False
            page.update()
            
            time.sleep(0.5)
            logging.info("嘗試播放音效")
            audio_player.play()
        except Exception as e:
            logging.error(traceback.format_exc())
            show_error(str(e))
            panel_player.visible = False
            page.update()

    def on_upload_result(e: ft.FilePickerUploadEvent):
        logging.info(f"上傳事件觸發: progress={e.progress}, file={e.file_name}")
        if e.progress == 1.0:
            status_text.value = "上傳完成，AI 讀取中..."
            page.update()
            file_name = e.file_name
            file_path = os.path.join(page.upload_dir, file_name)
            try:
                logging.info(f"讀取上傳檔案: {file_path}")
                with open(file_path, "rb") as f: image_bytes = f.read()
                img_display.visible = False 
                threading.Thread(target=run_process_in_thread, args=(image_bytes, current_mode["is_detailed"]), daemon=True).start()
            except Exception as err: 
                logging.error(traceback.format_exc())
                show_error(f"讀取檔案失敗: {err}")

    def on_file_picked(e: ft.FilePickerResultEvent):
        if e.files:
            file_obj = e.files[0]
            cleanup()
            status_text.value = "正在上傳圖片..."
            status_text.color = "grey"
            panel_player.visible = False
            page.update()
            logging.info(f"使用者選擇了檔案: {file_obj.name}, 開始上傳...")
            upload_url = page.get_upload_url(file_obj.name, 600)
            file_picker.upload([ft.FilePickerUploadFile(file_obj.name, upload_url)])
        else:
            logging.info("使用者取消了檔案選擇")

    file_picker = ft.FilePicker(on_result=on_file_picked, on_upload=on_upload_result)
    page.overlay.append(file_picker)

    def play_pause_click(e):
        if btn_play.icon == "pause_circle":
            audio_player.pause()
            btn_play.icon = "play_circle"
        else:
            if btn_play.icon == "replay_circle": audio_player.seek(0)
            audio_player.resume()
            btn_play.icon = "pause_circle"
        page.update()

    def slider_event(e):
        nonlocal is_seeking
        if e.event_type == "change_start": is_seeking = True
        elif e.event_type == "change_end":
            audio_player.seek(int(slider_progress.value))
            is_seeking = False

    def on_position_changed(e):
        nonlocal current_duration
        if not is_seeking:
            # ★ 關鍵優化：緩存 Duration，避免頻繁查詢導致無聲電腦 Timeout
            if current_duration == 0:
                try:
                    # 只有還不知道長度時才去問
                    d = audio_player.get_duration()
                    if d: current_duration = d
                except: pass # 如果問不到(無聲電腦)，就裝作沒事，不要報錯

            pos = float(e.data)
            dur = current_duration
            
            if dur > 0:
                slider_progress.max = dur
                slider_progress.value = min(pos, dur)
                txt_duration.value = f"{int(pos//1000)//60:02}:{int(pos//1000)%60:02} / {int(dur//1000)//60:02}:{int(dur//1000)%60:02}"
                page.update()

    def on_player_state_changed(e):
        if e.data == "completed":
            btn_play.icon = "replay_circle"
            btn_play.icon_color = "green"
            status_text.value = "播放完畢"
            page.update()

    audio_player.on_position_changed = on_position_changed
    audio_player.on_state_changed = on_player_state_changed
    btn_play.on_click = play_pause_click
    slider_progress.on_change_start = slider_event
    slider_progress.on_change_end = slider_event

    def mode_click(is_detailed):
        current_mode["is_detailed"] = is_detailed
        file_picker.pick_files(allow_multiple=False, file_type=ft.FilePickerFileType.IMAGE)

    page.add(
        ft.Container(height=10),
        ft.Text(APP_TITLE, size=24, weight="bold", color="#1976D2", text_align=ft.TextAlign.CENTER),
        ft.Divider(),
        ft.Container(content=img_display, alignment=ft.alignment.center, height=250),
        ft.Container(content=panel_player, bgcolor="#F0F0F0", padding=10, border_radius=10),
        ft.Container(height=10),
        ft.Row([
            ft.ElevatedButton(" 簡略模式 ", icon="short_text", on_click=lambda e: mode_click(False), expand=True),
            ft.ElevatedButton(" 照片模式 ", icon="description", on_click=lambda e: mode_click(True), expand=True),
        ], spacing=20),
        ft.Divider(),
        scroll_container
    )
    if not GEMINI_API_KEY or not YATING_API_KEY: show_error("啟動失敗：找不到 API Key")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 0))
    print("Application started.")
    
    # 建立必要的資料夾 (uploads 和 assets)
    os.makedirs("uploads", exist_ok=True)
    os.makedirs("assets", exist_ok=True)

    os.environ["FLET_SECRET_KEY"] = "GrandmaSecretKey2025"
    
    # ★★★ 智慧啟動邏輯 (保留您測試成功的設定) ★★★
    try:
        print("🚀 嘗試以 [公開模式] 啟動 (手機可連線)...")
        ft.app(
            target=main, 
            view=ft.AppView.WEB_BROWSER, 
            port=port,
            host="0.0.0.0", 
            upload_dir="uploads",
            assets_dir="assets" # 確保 Flet 知道去哪裡找音檔
        )
    except Exception as e:
        print(f"⚠️ 公開模式啟動失敗: {e}")
        print("🔄 自動切換為 [本機模式]...")
        ft.app(
            target=main, 
            view=ft.AppView.WEB_BROWSER, 
            port=port,
            upload_dir="uploads",
            assets_dir="assets"
        )
