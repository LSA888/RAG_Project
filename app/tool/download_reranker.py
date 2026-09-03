import time
from modelscope.hub.snapshot_download import snapshot_download

local_dir = r"D:\ai_models\modelscope_cache\models\rerank"

# 支持断点续传：中断后重新运行，会从已下载的部分继续
MAX_RETRIES = 20

for attempt in range(1, MAX_RETRIES + 1):
    try:
        print(f"第 {attempt}/{MAX_RETRIES} 次尝试下载 bge-reranker-large ...")
        snapshot_download(
            model_id="BAAI/bge-reranker-large",
            cache_dir=local_dir,
            # 只下载 FlagReranker 实际加载需要的文件：
            # 配置/分词器(小文件) + 核心权重 model.safetensors(约2.2GB)
            # 跳过冗余的 onnx/ 目录和 pytorch_model.bin(与safetensors重复)，避免重复下载数GB
            allow_patterns=[
                "config.json",
                "configuration.json",
                "tokenizer.json",
                "tokenizer_config.json",
                "special_tokens_map.json",
                "sentencepiece.bpe.model",
                "model.safetensors",
            ],
        )
        print("下载完成，模型目录：", local_dir)
        break
    except Exception as e:
        print(f"下载中断: {type(e).__name__}: {e}")
        if attempt == MAX_RETRIES:
            print("达到最大重试次数，下载失败。请检查网络后重新运行本脚本（已下载的部分不会丢失）。")
            raise
        wait = 5
        print(f"{wait} 秒后自动重试（断点续传，不会从头下载）...")
        time.sleep(wait)
