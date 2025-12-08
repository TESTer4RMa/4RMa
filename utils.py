import time
import functools
import logging
from typing import Callable, Any

LOGGER_NAME = "GrandmaReader"

def setup_logging(log_file: str = "app.log") -> None:
    """初始化日誌系統 (只應在 main entry point 呼叫一次)"""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    
    # 避免重複添加 Handler
    if logger.handlers:
        return

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    # File Handler
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Stream Handler (Console)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

def get_logger() -> logging.Logger:
    """獲取全域 logger"""
    return logging.getLogger(LOGGER_NAME)

def time_it(func: Callable[..., Any]) -> Callable[..., Any]:
    """裝飾器：自動計算並記錄函數執行時間"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logger = get_logger()
        start = time.time()
        try:
            result = func(*args, **kwargs)
            end = time.time()
            logger.info(f"⏱️ [{func.__name__}] 耗時: {end - start:.2f} 秒")
            return result
        except Exception as e:
            logger.error(f"❌ [{func.__name__}] 發生錯誤: {str(e)}")
            raise e
    return wrapper
