"""
Custom Audio Converter for MarkItDown
支持长音频处理的增强版 AudioConverter

使用方法：
    from markitdown import MarkItDown
    from markitdown_mcp.custom_audio_converter import CustomAudioConverter

    md = MarkItDown()
    converter = CustomAudioConverter(language="en-US", chunk_length_ms=30000)
    md.register_converter(converter, priority=-10)

    result = md.convert("long_video.mp4")
"""

import io
import os
import math
from typing import BinaryIO, Any
from datetime import datetime
from pydub import AudioSegment
import speech_recognition as sr
from markitdown._base_converter import DocumentConverter, DocumentConverterResult
from markitdown._stream_info import StreamInfo


class CustomAudioConverter(DocumentConverter):
    """
    增强版 AudioConverter，支持长音频处理

    特性：
    - 支持指定识别语言
    - 自动分段处理长音频
    - 避免 Google API 超时限制
    - 生成带时间戳的分段转录
    """

    def __init__(self, language="en-US", chunk_length_ms=30000):
        """
        初始化转换器

        Args:
            language: 语音识别语言（如 'en-US', 'zh-CN'）
            chunk_length_ms: 分段长度（毫秒），默认 30 秒
        """
        self.language = language
        self.chunk_length_ms = chunk_length_ms

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
        转换音频/视频文件为 Markdown
        """
        # 确定音频格式
        if stream_info.extension == ".wav" or stream_info.mimetype == "audio/x-wav":
            audio_format = "wav"
        elif stream_info.extension == ".mp3" or stream_info.mimetype == "audio/mpeg":
            audio_format = "mp3"
        elif stream_info.extension in [".mp4", ".m4a"] or stream_info.mimetype == "video/mp4":
            audio_format = "mp4"
        else:
            audio_format = "mp4"  # 默认

        # 提取音频
        print(f"[CustomAudioConverter] Extracting audio from {audio_format} file...")
        audio_segment = AudioSegment.from_file(file_stream, format=audio_format)
        duration_seconds = len(audio_segment) / 1000

        print(f"[CustomAudioConverter] Audio duration: {duration_seconds:.2f} seconds ({int(duration_seconds//60)} min {int(duration_seconds%60)} sec)")

        # 分段处理
        num_chunks = math.ceil(len(audio_segment) / self.chunk_length_ms)
        print(f"[CustomAudioConverter] Processing in {num_chunks} chunks of {self.chunk_length_ms/1000:.0f} seconds each...")

        recognizer = sr.Recognizer()
        transcriptions = []

        for i in range(num_chunks):
            start_ms = i * self.chunk_length_ms
            end_ms = min((i + 1) * self.chunk_length_ms, len(audio_segment))
            chunk = audio_segment[start_ms:end_ms]

            # 转换为 WAV
            wav_io = io.BytesIO()
            chunk.export(wav_io, format="wav")
            wav_io.seek(0)

            # 识别
            print(f"  Chunk {i+1}/{num_chunks} ({start_ms/1000:.0f}s-{end_ms/1000:.0f}s)...", end=" ")

            try:
                with sr.AudioFile(wav_io) as source:
                    audio_data = recognizer.record(source)

                # 使用指定语言识别
                text = recognizer.recognize_google(audio_data, language=self.language)
                transcriptions.append({
                    'start': start_ms / 1000,
                    'end': end_ms / 1000,
                    'text': text
                })
                print(f"OK ({len(text)} chars)")

            except sr.UnknownValueError:
                print("No speech")
                transcriptions.append({
                    'start': start_ms / 1000,
                    'end': end_ms / 1000,
                    'text': '[No speech detected]'
                })
            except sr.RequestError as e:
                print(f"API error: {e}")
                transcriptions.append({
                    'start': start_ms / 1000,
                    'end': end_ms / 1000,
                    'text': f'[API error: {e}]'
                })
            except Exception as e:
                print(f"Error: {e}")
                transcriptions.append({
                    'start': start_ms / 1000,
                    'end': end_ms / 1000,
                    'text': f'[Error: {e}]'
                })

        # 生成完整转录文本（用于摘要）
        full_text = " ".join([t['text'] for t in transcriptions if not t['text'].startswith('[')])

        # 生成 Markdown
        md_content = f"### 音频摘要\n\n"

        # 生成简短摘要（基于转录内容）
        if len(full_text) > 200:
            # 取前 200 字作为简要摘要
            summary = full_text[:200] + "..."
            md_content += f"{summary}\n\n"
            md_content += f"**注**: 这是音频内容的前 200 字预览。完整转录请查看下方分段内容。\n\n"
        else:
            md_content += f"{full_text}\n\n"

        # 音频信息
        md_content += f"### Audio Information\n\n"
        md_content += f"- **Duration**: {int(duration_seconds//60)} min {int(duration_seconds%60)} sec\n"
        md_content += f"- **Channels**: {audio_segment.channels}\n"
        md_content += f"- **Sample Rate**: {audio_segment.frame_rate} Hz\n"
        md_content += f"- **Recognition Language**: {self.language}\n"
        md_content += f"- **Processing Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        # 分段转录
        md_content += "### Segmented Transcript (with Timestamps)\n\n"
        for segment in transcriptions:
            start = segment['start']
            end = segment['end']
            text = segment['text']
            md_content += f"**[{int(start//60):02d}:{int(start%60):02d} - {int(end//60):02d}:{int(end%60):02d}]**\n\n"
            md_content += f"{text}\n\n"

        return DocumentConverterResult(markdown=md_content.strip())


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
