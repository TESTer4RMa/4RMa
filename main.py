import flet as ft
import os
import uuid
import threading
import time
import asyncio
import warnings
from typing import Optional

from config import AppConfig
from services import GeminiService, YatingTTSService
from utils import setup_logging, get_logger

# 忽略 Flet 的 Audio Deprecation Warning
warnings.filterwarnings("ignore", category=DeprecationWarning)

class GrandmaReaderApp:
    def __init__(self, page: ft.Page, config: AppConfig):
        self.page = page
        self.config = config
        self.logger = get_logger()
        self.session_id = str(uuid.uuid4())[:8]
        
        # Dependency Injection
        self.gemini_service = GeminiService(config)
        self.tts_service = YatingTTSService(config)
        
        # State Management
        self.is_detailed_mode = False
        self.is_seeking = False
        self.processing_lock = threading.Lock()
        
        # 動態建立播放器
        self.audio_player: Optional[ft.Audio] = None 

        # Init
        self.setup_page()
        self.build_ui_components()
        self.layout_ui()
        
        # 初始掛載，但不使用 Base64，直接給空或靜音檔
        # 這裡我們暫時不掛載任何音訊，等有檔案再掛載
        self.logger.info("應用程式初始化完成")

    def setup_page(self):
        """頁面基礎設定"""
        self.page.title = self.config.APP_TITLE
        self.page.bgcolor = self.config.UI_COLORS["app_bgcolor"]
        self.page.padding = 20
        # 確保資料夾存在
        os.makedirs("uploads", exist_ok=True)
        os.makedirs("assets", exist_ok=True)

    def _remount_audio_player(self, audio_url: str):
        """
        Robert 的核彈級重置：使用 URL 載入音訊
        """
        # 1. 移除舊的
        if self.audio_player in self.page.overlay:
            self.page.overlay.remove(self.audio_player)
        
        # 2. 建立新的，使用 src (URL) 而非 src_base64
        # audio_url 應該是 "/filename.wav" 格式
        self.audio_player = ft.Audio(
            src=audio_url,  # <--- 關鍵修改：使用 URL
            autoplay=False,
            release_mode="stop",
            on_position_changed=self.on_player_position_changed,
            on_state_changed=self.on_player_state_changed,
            on_loaded=lambda e: self.logger.info(f"音訊已載入: {audio_url}") # 監聽載入事件
        )
        
        # 3. 加入並更新
        self.page.overlay.append(self.audio_player)
        self.page.update()
        self.logger.info(f"Audio Player 重建完成，來源: {audio_url}")

    def build_ui_components(self):
        """初始化所有 UI 元件"""
        colors = self.config.UI_COLORS

        # 1. 檔案選擇器
        self.file_picker = ft.FilePicker(
            on_result=self.on_file_picked, 
            on_upload=self.on_upload_result
        )
        self.page.overlay.append(self.file_picker)

        # 2. 標題與除錯區
        self.txt_result = ft.Text("", size=16, color="black", selectable=True)
        self.container_result = ft.Container(
            content=ft.Column([
                ft.Text("【辨識結果】", size=14, color="blue", weight="bold"),
                ft.Column([self.txt_result], scroll=ft.ScrollMode.AUTO, expand=True)
            ]),
            bgcolor="white", padding=10, border_radius=10, 
            visible=False, height=200, border=ft.border.all(1, "grey")
        )
        
        self.btn_debug = ft.IconButton(
            icon="visibility_off", icon_color="grey", 
            tooltip="顯示/隱藏文字", on_click=self.toggle_debug
        )
        
        self.header = ft.Row([
            ft.Text("👵 阿嬤的讀信機", size=24, weight="bold", color=colors["text_color_primary"]),
            ft.Container(expand=True), 
            self.btn_debug
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

        # 3. 中間狀態區
        self.icon_status = ft.Icon(name="camera_alt_rounded", size=120, color=colors["status_icon_idle"])
        self.spinner_status = ft.ProgressRing(width=80, height=80, stroke_width=8, color="#2196F3", visible=False)
        self.lbl_status = ft.Text("請選擇模式\n開始拍照", size=24, weight="bold", 
                                  color=colors["text_color_primary"], text_align=ft.TextAlign.CENTER)
        
        self.center_area = ft.Container(
            content=ft.Column([
                ft.Container(height=20),
                ft.Container(
                    content=ft.Stack([
                        ft.Container(content=self.icon_status, alignment=ft.alignment.center),
                        ft.Container(content=self.spinner_status, alignment=ft.alignment.center),
                    ]), height=150
                ),
                self.lbl_status,
                ft.Container(height=10),
                self.container_result
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            expand=True
        )

        # 4. 播放控制條
        self.btn_play_pause = ft.IconButton(
            icon="play_circle_fill", icon_size=40, icon_color=colors["status_icon_speaking"],
            on_click=self.cmd_play_pause
        )
        self.slider_progress = ft.Slider(
            min=0, max=1000, value=0, expand=True, 
            active_color=colors["status_icon_speaking"], inactive_color="#E0E0E0", thumb_color="green",
            on_change_start=self.on_seek_start, on_change_end=self.on_seek_end
        )
        self.txt_time = ft.Text("00:00 / 00:00", size=14, color=colors["text_color_secondary"], weight="bold")
        
        self.player_bar = ft.Container(
            content=ft.Row([self.btn_play_pause, self.slider_progress, self.txt_time], alignment=ft.MainAxisAlignment.CENTER),
            bgcolor="white", padding=10, border_radius=50,
            shadow=ft.BoxShadow(spread_radius=1, blur_radius=5, color="#20000000", offset=ft.Offset(0, 2)),
            visible=False, margin=ft.margin.only(bottom=15)
        )

        # 5. 底部按鈕
        self.btn_mode_simple = self._create_mode_btn("short_text", "簡略模式", colors["btn_simple_color"], False)
        self.btn_mode_detailed = self._create_mode_btn("description", "照片模式", colors["btn_detailed_color"], True)

    def _create_mode_btn(self, icon: str, text: str, color: str, is_detailed: bool) -> ft.Container:
        return ft.Container(
            content=ft.Row([ft.Icon(icon, color="white"), ft.Text(text, color="white", size=20)], alignment=ft.MainAxisAlignment.CENTER),
            bgcolor=color, padding=15, border_radius=50,
            on_click=lambda e: self.on_mode_click(is_detailed), expand=True
        )

    def layout_ui(self):
        """組合佈局"""
        self.page.add(
            ft.Column([
                self.header,
                self.center_area,
                ft.Container(
                    content=ft.Column([
                        self.player_bar,
                        ft.Row([self.btn_mode_simple, ft.Container(width=10), self.btn_mode_detailed])
                    ]),
                    padding=ft.margin.only(bottom=10)
                )
            ], expand=True)
        )

    # --- 狀態更新 ---

    def toggle_debug(self, e):
        self.container_result.visible = not self.container_result.visible
        self.btn_debug.icon = "visibility" if self.container_result.visible else "visibility_off"
        self.page.update()

    def update_ui_status(self, state: str, error_msg: Optional[str] = None):
        colors = self.config.UI_COLORS
        
        if state == "idle":
            self.icon_status.visible = True
            self.spinner_status.visible = False
            self.icon_status.name = "camera_alt_rounded"
            self.icon_status.color = colors["status_icon_idle"]
            self.lbl_status.value = "請選擇模式\n開始拍照"
            self.player_bar.visible = False
            
        elif state == "uploading":
            self.icon_status.visible = False
            self.spinner_status.visible = True
            self.lbl_status.value = "上傳中..."
            self.player_bar.visible = False
            
        elif state == "thinking":
            self.icon_status.visible = True
            self.spinner_status.visible = True
            self.icon_status.name = "psychology"
            self.icon_status.color = colors["status_icon_thinking"]
            self.lbl_status.value = "阿嬤修等幾勒\n我咧看信..."
            
        elif state == "ready":
            self.icon_status.visible = True
            self.spinner_status.visible = False
            self.icon_status.name = "volume_up_rounded"
            self.icon_status.color = colors["status_icon_speaking"]
            self.lbl_status.value = "讀好囉！\n請按播放鍵"
            self.player_bar.visible = True
            self.btn_play_pause.icon = "play_circle_fill"
            self.slider_progress.value = 0
            self.txt_time.value = "00:00 / 00:00"
            
        elif state == "speaking":
            self.icon_status.visible = True
            self.spinner_status.visible = False
            self.icon_status.name = "record_voice_over"
            self.icon_status.color = colors["status_icon_speaking"]
            self.lbl_status.value = "正在讀給你聽..."
            self.player_bar.visible = True
            self.btn_play_pause.icon = "pause_circle_filled"
            
        elif state == "error":
            self.icon_status.visible = True
            self.spinner_status.visible = False
            self.icon_status.name = "error_outline"
            self.icon_status.color = colors["status_icon_error"]
            self.lbl_status.value = "讀取失敗"
            if error_msg:
                self.txt_result.value = f"錯誤: {error_msg}"
                self.container_result.visible = True
                
        self.page.update()

    # --- 核心業務邏輯 ---

    def process_image_task(self, image_bytes: bytes):
        """背景處理任務"""
        with self.processing_lock:
            try:
                self.update_ui_status("thinking")
                
                # 1. AI 辨識
                prompt = self.config.PROMPT_DETAILED if self.is_detailed_mode else self.config.PROMPT_SIMPLE
                text = self.gemini_service.get_intent(image_bytes, prompt)
                
                self.txt_result.value = text
                self.container_result.visible = True
                self.btn_debug.icon = "visibility"
                self.page.update()

                # 2. TTS 合成
                wav_bytes = self.tts_service.synthesize(text)
                
                # 3. 儲存 (使用唯一檔名)
                unique_filename = f"audio_{self.session_id}_{int(time.time())}.wav"
                output_path = os.path.join("assets", unique_filename)
                
                with open(output_path, "wb") as f:
                    f.write(wav_bytes)

                # 4. 核彈級重載播放器 - 改用 URL
                # Flet 映射規則： assets/xxx.wav -> /xxx.wav
                audio_url = f"/{unique_filename}"
                self._remount_audio_player(audio_url)
                
                # 5. 更新 UI
                self.update_ui_status("ready")
                
            except Exception as e:
                self.logger.error(f"Task Failed: {e}", exc_info=True)
                self.update_ui_status("error", str(e))

    # --- 事件處理 ---

    def on_mode_click(self, is_detailed: bool):
        self.is_detailed_mode = is_detailed
        self.file_picker.pick_files(allow_multiple=False, file_type=ft.FilePickerFileType.IMAGE)

    def on_file_picked(self, e: ft.FilePickerResultEvent):
        if e.files:
            self.update_ui_status("uploading")
            f = e.files[0]
            upload_url = self.page.get_upload_url(f.name, 600)
            self.file_picker.upload([ft.FilePickerUploadFile(f.name, upload_url)])
        else:
            self.update_ui_status("idle")

    def on_upload_result(self, e: ft.FilePickerUploadEvent):
        if e.progress == 1.0:
            file_path = os.path.join("uploads", e.file_name)
            try:
                with open(file_path, "rb") as f: 
                    image_bytes = f.read()
                threading.Thread(target=self.process_image_task, args=(image_bytes,), daemon=True).start()
            except Exception as err:
                self.logger.error(f"File Read Error: {err}")
                self.update_ui_status("error", str(err))

    # --- 播放器 UI 連動 ---

    def cmd_play_pause(self, e):
        is_playing = self.btn_play_pause.icon == "pause_circle_filled"
        
        if is_playing:
            self.audio_player.pause()
            self.btn_play_pause.icon = "play_circle_fill"
        else:
            # Robert: 確保 Audio 元件已經掛載
            if not self.audio_player:
                return

            # 如果進度條在開頭，強制 play
            if self.slider_progress.value <= 10: 
                self.audio_player.play()
            else:
                self.audio_player.resume()
                
            self.btn_play_pause.icon = "pause_circle_filled"
            self.update_ui_status("speaking")
            
        self.page.update()

    def on_seek_start(self, e):
        self.is_seeking = True

    def on_seek_end(self, e):
        self.is_seeking = False
        self.audio_player.pause()
        pos_ms = int(self.slider_progress.value)
        self.audio_player.seek(pos_ms)
        self.page.run_task(self._resume_after_seek)

    async def _resume_after_seek(self):
        await asyncio.sleep(0.1) 
        self.audio_player.resume()
        self.btn_play_pause.icon = "pause_circle_filled"
        self.page.update()

    def on_player_position_changed(self, e):
        if not self.is_seeking:
            pos = float(e.data)
            
            # Robert Fix: 為 get_duration 加上錯誤處理
            # 當瀏覽器還在解碼 WAV 時，get_duration 可能會 Timeout
            try:
                dur = self.audio_player.get_duration()
            except Exception:
                # 若獲取失敗，先設為 0，避免 Crash，等待下一次更新
                dur = 0
            
            # 只有當 duration 有效時才更新
            if dur and dur > 0:
                self.slider_progress.max = dur
                self.slider_progress.value = min(pos, dur)
                p_m, p_s = divmod(int(pos/1000), 60)
                d_m, d_s = divmod(int(dur/1000), 60)
                self.txt_time.value = f"{p_m:02}:{p_s:02} / {d_m:02}:{d_s:02}"
                self.page.update()

    def on_player_state_changed(self, e):
        if e.data == "completed":
            self.btn_play_pause.icon = "play_circle_fill"
            self.slider_progress.value = 0
            self.audio_player.autoplay = False 
            self.update_ui_status("ready") 
            self.page.update()

def main(page: ft.Page):
    config = AppConfig.load_from_env()
    setup_logging(config.LOG_FILE)
    GrandmaReaderApp(page, config)

if __name__ == "__main__":
    os.environ["FLET_SECRET_KEY"] = "GrandmaSecret2025"
    # Robert Note: assets_dir 設定非常重要，它將 "assets" 資料夾映射到 Web Root 的 "/"
    ft.app(target=main, view=ft.AppView.WEB_BROWSER, upload_dir="uploads", assets_dir="assets")
