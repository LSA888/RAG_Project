import time
from modelscope.hub.snapshot_download import snapshot_download

# 下载模型到 D:/ai_models/modelscope_cache/models/BAAI/bge-m3
# 支持断点续传：中断后重新运行，会从已下载的部分继续
MAX_RETRIES = 20

for attempt in range(1, MAX_RETRIES + 1):
    try:
        print(f"第 {attempt}/{MAX_RETRIES} 次尝试下载 BGE-M3 ...")
        model_dir = snapshot_download(
            'BAAI/bge-m3',
            cache_dir='D:/ai_models/modelscope_cache/models',
        )
        print(f"模型已下载到: {model_dir}")
        break
    except Exception as e:
        print(f"下载中断: {type(e).__name__}: {e}")
        if attempt == MAX_RETRIES:
            print("达到最大重试次数，下载失败。请检查网络后重新运行本脚本（已下载的部分不会丢失）。")
            raise
        wait = 5
        print(f"{wait} 秒后自动重试（断点续传，不会从头下载）...")
        time.sleep(wait)
