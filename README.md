👵 阿嬤的讀信機 (Grandma's Reader) - v4.1 (Robust Edition)
"程式碼是給人看的，順便給機器執行。" —— Robert C. Martin (Uncle Bob)
1. 專案簡介
本專案是一個專為長輩設計的 Web App (PWA)，功能是「將圖片中的文字轉換為台語語音朗讀」。
核心目標是極簡操作與容錯性，解決長輩看不懂信件、藥單或簡訊的困擾。
v4.1 重構與優化重點 (Refactoring Highlights)：
架構優化 (Clean Architecture)：徹底分離 UI (main.py)、業務邏輯 (services.py) 與配置 (config.py)。
高魯棒性 TTS (Robustness)：
Fail Fast：嚴格檢查語音片段完整性，絕不播放殘缺內容。
Smart Chunking：將文本切分為更小的片段 (80字)，大幅降低 API Timeout 機率。
Resilience：實作指數退避與重試機制 (Retry Pattern)，抵抗網路波動。
無副作用設計：日誌系統改為 Lazy Loading，消除 Import Side Effects。
解決競態條件：修復 Flet Audio 組件的非同步操作問題。
2. 系統架構與檔案說明 (Architecture)
本專案採用 模組化架構，確保每個檔案只做一件事 (Single Responsibility Principle)。
檔案名稱
職責說明 (Responsibilities)
關鍵技術
main.py
應用程式入口 (Controller/View)。

負責 Flet UI 構建、事件監聽與協調各服務運作，不包含任何業務邏輯。
flet, asyncio, threading
config.py
配置管理層 (Configuration)。

單一真理來源 (Single Source of Truth)。集中管理所有參數（API Keys, UI Colors, TTS Tuning），支援熱抽換。
dataclass
services.py
業務邏輯層 (Business Logic)。

封裝 Gemini AI 辨識與雅婷 TTS 合成邏輯。實作了並發下載與錯誤處理。
google.generativeai, concurrent.futures, wave
utils.py
工具層 (Utilities)。

提供全域日誌記錄 (logger) 與效能監控裝飾器 (@time_it)。
logging, functools
ui_config.json
外觀設定檔 (Optional)。

若存在，將自動覆蓋 config.py 中的預設顏色設定。
JSON

3. 核心資料流 (Data Flow)
初始化：GrandmaReaderApp 啟動，注入 AppConfig 至各服務。
圖片上傳：使用者選取圖片 -> 啟動背景執行緒處理。
意圖辨識 (Gemini)：
自動選擇最佳模型 (Flash 優先)。
根據模式 (簡略/詳細) 注入對應 Prompt。
語音合成 (Yating TTS) - Robust Pipeline：
切分 (Splitting)：將長文本切分為 80字 的微小片段。
並發 (Concurrency)：使用 ThreadPool (Max Workers: 2) 平行下載。
完整性檢查 (Integrity Check)：若有任何片段失敗，拋出異常並中止，確保不播放錯誤資訊。
合併 (Merging)：在記憶體中合併 WAV 串流。
播放：寫入暫存檔，設定 autoplay=True 觸發 Flet 播放器。
4. 安裝與設定 (Setup)
環境需求
Python 3.10+
套件依賴：請參考 requirements.txt
API Key 設定
支援 環境變數 與 本地檔案 (優先權：Env > File)。
Google Gemini API: GEMINI_API_KEY 或 Gemini_API.txt
雅婷逐字稿 TTS API: YATING_API_KEY 或 Yating_API.txt
Flet Secret: FLET_SECRET_KEY (部署時必填)
5. 進階參數調優 (Tuning Guide)
所有可調整參數皆位於 config.py，修改這些參數不需要動到 services.py。
TTS 穩定性調整
若遇到網路不穩或 API 限制，請調整以下參數：
# config.py -> AppConfig

TTS_MAX_WORKERS: int = 2       # 並發數。若 API 擋 IP，請降為 1。
TTS_TIMEOUT: int = 15          # 單次請求超時秒數。越短失敗判定越快 (Fail Fast)。
TTS_CHUNK_SIZE: int = 80       # 切片大小。越小越穩定，但請求次數會變多。


Prompt (提示詞) 修改
簡略模式：修改 prompt_simple.txt 或 config.py 中的 PROMPT_SIMPLE。
照片模式：修改 prompt_detailed.txt 或 config.py 中的 PROMPT_DETAILED。
6. 開發者備忘錄 (Developer Notes)
Thread Safety: UI 操作請務必在主執行緒或使用 page.update()。
Asyncio: Flet 的 page.run_task 需搭配 async def 函式。在非同步函式中，必須使用 await asyncio.sleep() 而非 time.sleep()，否則會阻塞整個 UI。
Logging: 使用 self.logger.info() 取代 print()。日誌會同時輸出到 Console 與 app.log。
Maintained by Robert ("Uncle Bob")'s Refactoring Service
Last Updated: 2025-12-08
