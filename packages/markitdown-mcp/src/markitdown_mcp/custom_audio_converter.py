"""
Custom Audio Converter for MarkItDown
使用双引擎 Web Speech API（中文 + 英文）并行处理

特性：
- 中英文双引擎并行识别
- 返回双份转录结果
- 由 Claude Code 智能合并
- 适合中英文混合场景
"""

import io
import os
import sys
import math
import time
import threading
import concurrent.futures
from typing import BinaryIO, Any, List, Dict
from datetime import datetime
from pydub import AudioSegment
import speech_recognition as sr
from markitdown._base_converter import DocumentConverter, DocumentConverterResult
from markitdown._stream_info import StreamInfo


class CustomAudioConverter(DocumentConverter):
    """
    双引擎音频转换器（中文 + 英文）

    特性：
    - 同时使用中文和英文引擎识别
    - 返回双份转录结果
    - 由 Claude Code 智能合并
    - 资源自动清理，避免内存泄漏
    """

    def __init__(self, chunk_length_ms=30000):
        """
        初始化转换器

        Args:
            chunk_length_ms: 分段长度（毫秒），默认 30 秒
        """
        self.chunk_length_ms = chunk_length_ms
        # 重用线程池，避免重复创建销毁
        self._executor = None

    def _get_executor(self):
        """获取或创建线程池（重用，避免重复创建）"""
        if self._executor is None:
            self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        return self._executor

    def _cleanup_executor(self):
        """清理线程池"""
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None

    def accepts(self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any) -> bool:
        """检查是否支持此文件格式"""
        extension = (stream_info.extension or "").lower()
        mimetype = (stream_info.mimetype or "").lower()

        # 支持的文件扩展名
        accepted_extensions = [".wav", ".mp3", ".m4a", ".mp4"]

        # 支持的 MIME 类型
        accepted_mimetypes = ["audio/x-wav", "audio/mpeg", "video/mp4"]

        if extension in accepted_extensions:
            return True

        for mime in accepted_mimetypes:
            if mimetype.startswith(mime):
                return True

        return False

    def convert(self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any) -> DocumentConverterResult:
        """
        使用双引擎并行转换音频/视频文件为 Markdown
        """
        try:
            return self._convert_internal(file_stream, stream_info, **kwargs)
        finally:
            # 确保资源清理
            self._cleanup_executor()

    def _convert_internal(self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any) -> DocumentConverterResult:
        """内部转换逻辑"""
        # 确定音频格式
        if stream_info.extension == ".wav" or stream_info.mimetype == "audio/x-wav":
            audio_format = "wav"
        elif stream_info.extension == ".mp3" or stream_info.mimetype == "audio/mpeg":
            audio_format = "mp3"
        elif stream_info.extension in [".mp4", ".m4a"] or stream_info.mimetype == "video/mp4":
            audio_format = "mp4"
        else:
            audio_format = "mp4"

        # 提取音频
        print(f"[DualEngine] Extracting audio from {audio_format} file...", flush=True)
        sys.stdout.flush()

        audio_segment = AudioSegment.from_file(file_stream, format=audio_format)
        duration_seconds = len(audio_segment) / 1000

        print(f"[DualEngine] Audio duration: {duration_seconds:.2f} seconds ({int(duration_seconds//60)} min {int(duration_seconds%60)} sec)", flush=True)
        sys.stdout.flush()

        # 分段处理
        num_chunks = math.ceil(len(audio_segment) / self.chunk_length_ms)
        print(f"[DualEngine] Processing {num_chunks} chunks with parallel dual engines (Chinese + English)...", flush=True)
        print(f"[DualEngine] Active threads at start: {threading.active_count()}", flush=True)
        sys.stdout.flush()

        chinese_results = []
        english_results = []

        # 获取重用的线程池
        executor = self._get_executor()

        for i in range(num_chunks):
            start_ms = i * self.chunk_length_ms
            end_ms = min((i + 1) * self.chunk_length_ms, len(audio_segment))
            chunk = audio_segment[start_ms:end_ms]

            print(f"  Chunk {i+1}/{num_chunks} ({start_ms/1000:.0f}s-{end_ms/1000:.0f}s) - parallel processing...", flush=True)
            sys.stdout.flush()

            # 准备两个独立的 BytesIO（避免竞态条件）
            wav_io_zh = io.BytesIO()
            wav_io_en = io.BytesIO()

            try:
                chunk.export(wav_io_zh, format="wav")
                wav_io_zh.seek(0)

                chunk.export(wav_io_en, format="wav")
                wav_io_en.seek(0)

                # 使用重用的线程池并行处理
                recognizer_zh = sr.Recognizer()
                recognizer_en = sr.Recognizer()

                # 提交并行任务
                future_zh = executor.submit(
                    self._transcribe_chunk,
                    recognizer_zh,
                    wav_io_zh,
                    "zh-CN",
                    "Chinese"
                )
                future_en = executor.submit(
                    self._transcribe_chunk,
                    recognizer_en,
                    wav_io_en,
                    "en-US",
                    "English"
                )

                # 等待结果（90秒超时）
                try:
                    zh_text = future_zh.result(timeout=90)
                    en_text = future_en.result(timeout=90)
                except concurrent.futures.TimeoutError:
                    print("    Timeout: transcription took too long", flush=True)
                    sys.stdout.flush()
                    zh_text = "[Timeout after 90s]"
                    en_text = "[Timeout after 90s]"
                    # 注意：无法取消已运行的任务，但至少不会永远等待
                except Exception as e:
                    print(f"    Error in parallel processing: {e}", flush=True)
                    sys.stdout.flush()
                    zh_text = f"[Error: {e}]"
                    en_text = f"[Error: {e}]"

            finally:
                # 确保 BytesIO 被关闭，释放内存
                wav_io_zh.close()
                wav_io_en.close()

            # 保存结果
            chinese_results.append({
                'start': start_ms / 1000,
                'end': end_ms / 1000,
                'text': zh_text
            })
            english_results.append({
                'start': start_ms / 1000,
                'end': end_ms / 1000,
                'text': en_text
            })

            # 显式删除 chunk，释放内存
            del chunk

            # 添加延迟避免限流（除了最后一个分段）
            if i < num_chunks - 1:
                time.sleep(0.5)

        print(f"[DualEngine] All chunks processed successfully", flush=True)
        print(f"[DualEngine] Active threads at end: {threading.active_count()}", flush=True)
        sys.stdout.flush()

        # 生成双引擎结果 Markdown
        md_content = self._format_dual_results(chinese_results, english_results, duration_seconds, audio_segment)

        return DocumentConverterResult(markdown=md_content.strip())

    def _transcribe_chunk(self, recognizer: sr.Recognizer, wav_io: io.BytesIO, language: str, engine_name: str) -> str:
        """转录单个音频块"""
        try:
            with sr.AudioFile(wav_io) as source:
                audio_data = recognizer.record(source)

            text = recognizer.recognize_google(audio_data, language=language)
            print(f"    {engine_name}: OK ({len(text)} chars)", flush=True)
            sys.stdout.flush()
            return text

        except sr.UnknownValueError:
            print(f"    {engine_name}: No speech", flush=True)
            sys.stdout.flush()
            return '[No speech detected]'
        except sr.RequestError as e:
            print(f"    {engine_name}: API error - {e}", flush=True)
            print(f"    Error type: {type(e).__name__}", flush=True)
            sys.stdout.flush()
            return f'[API error: {e}]'
        except Exception as e:
            print(f"    {engine_name}: Unexpected error - {e}", flush=True)
            print(f"    Error type: {type(e).__name__}", flush=True)
            sys.stdout.flush()
            return f'[Error: {e}]'

    def _format_dual_results(self, chinese_results: List[Dict], english_results: List[Dict],
                            duration_seconds: float, audio_segment: AudioSegment) -> str:
        """格式化双引擎结果为 Markdown"""

        md_content = f"""# 双引擎转录结果（中文 + 英文）

## 音频信息
- **时长**: {int(duration_seconds//60)} 分 {int(duration_seconds%60)} 秒
- **声道**: {audio_segment.channels}
- **采样率**: {audio_segment.frame_rate} Hz
- **引擎**: Web Speech API (Google)
- **处理时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## 使用说明

下面是同一段音频的两份转录结果：
1. **中文引擎结果**：适合识别中文内容
2. **英文引擎结果**：适合识别英文内容

**请 Claude 智能合并这两份结果**，规则：
- 中文部分优先使用中文引擎结果
- 英文单词优先使用英文引擎结果
- 混合句子综合判断，保持语义流畅
- 输出最终的准确转录文本

---

"""

        # 逐段输出双引擎结果
        for i, (zh_seg, en_seg) in enumerate(zip(chinese_results, english_results), 1):
            start = zh_seg['start']
            end = zh_seg['end']

            md_content += f"### 分段 {i} [{int(start//60):02d}:{int(start%60):02d} - {int(end//60):02d}:{int(end%60):02d}]\n\n"
            md_content += f"**中文引擎**：{zh_seg['text']}\n\n"
            md_content += f"**英文引擎**：{en_seg['text']}\n\n"
            md_content += f"---\n\n"

        return md_content


# 示例使用
if __name__ == "__main__":
    """
    示例：使用自定义转换器处理长视频
    """
    from markitdown import MarkItDown

    # 设置 FFmpeg 路径（如果需要）
    ffmpeg_bin = os.path.join(
        os.environ.get('LOCALAPPDATA', ''),
        r"Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin"
    )
    if os.path.exists(ffmpeg_bin):
        os.environ["PATH"] = ffmpeg_bin + os.pathsep + os.environ.get("PATH", "")

    # 创建 MarkItDown 实例
    md = MarkItDown()

    # 注册自定义转换器（英文识别，30秒分段）
    custom_converter = CustomAudioConverter(language="en-US", chunk_length_ms=30000)
    md.register_converter(custom_converter, priority=-10)  # 高优先级

    # 转换视频
    video_file = "example.mp4"
    print(f"Converting {video_file} using MarkItDown + CustomAudioConverter...\n")

    result = md.convert(video_file)

    # 保存结果
    output_file = "output.md"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("# Video Transcription\n\n")
        f.write(f"**Source**: {video_file}\n")
        f.write("**Method**: MarkItDown + CustomAudioConverter + FFmpeg\n\n")
        f.write(result.text_content)

    print(f"\nTranscription complete! Output: {output_file}")
