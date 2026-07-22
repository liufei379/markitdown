import contextlib
import sys
import os
from collections.abc import AsyncIterator
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from mcp.server.sse import SseServerTransport
from starlette.requests import Request
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from markitdown import MarkItDown
import uvicorn

# Initialize MarkItDown with CustomAudioConverter
_md_instance = None

def get_markitdown_instance():
    """Get or create MarkItDown instance with CustomAudioConverter registered"""
    global _md_instance
    if _md_instance is None:
        _md_instance = MarkItDown(enable_plugins=check_plugins_enabled())

        # Register CustomAudioConverter for long audio/video files
        try:
            from .custom_audio_converter import CustomAudioConverter

            # Set FFmpeg path if available (Windows)
            if os.name == 'nt':
                ffmpeg_bin = os.path.join(
                    os.environ.get('LOCALAPPDATA', ''),
                    r"Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin"
                )
                if os.path.exists(ffmpeg_bin):
                    os.environ["PATH"] = ffmpeg_bin + os.pathsep + os.environ.get("PATH", "")

            # Register converter with faster-whisper (small model)
            # Using small model for best balance between accuracy and speed
            # Automatic device detection (CUDA if available, otherwise CPU)
            # Automatic compute type selection (int8_float16 for GPU, int8 for CPU)
            # Performance mode: balanced (30-50% faster, <1% accuracy loss)
            converter = CustomAudioConverter(
                model_size="small",
                device="auto",
                compute_type="auto",
                performance_mode="balanced"
            )
            _md_instance.register_converter(converter, priority=-10)
            print("[MarkItDown MCP] CustomAudioConverter registered successfully (faster-whisper optimized)")
        except ImportError as e:
            print(f"[MarkItDown MCP] Warning: Could not import CustomAudioConverter: {e}")
        except Exception as e:
            print(f"[MarkItDown MCP] Warning: Could not register CustomAudioConverter: {e}")

    return _md_instance

# Initialize FastMCP server for MarkItDown (SSE)
mcp = FastMCP("markitdown")


@mcp.tool()
async def convert_to_markdown(uri: str) -> str:
    """Convert a resource described by an http:, https:, file: or data: URI to markdown"""
    md = get_markitdown_instance()
    result = md.convert_uri(uri)

    # Check if image extraction is enabled (default: true)
    extract_images_enabled = os.getenv("MARKITDOWN_EXTRACT_IMAGES", "true").strip().lower() in ("true", "1", "yes")

    if extract_images_enabled and uri.startswith("file://"):
        # Convert URI to file path
        from urllib.parse import unquote
        file_path = unquote(uri.replace("file://", ""))

        # Handle Windows paths (remove leading / if path starts with drive letter)
        if file_path.startswith("/") and len(file_path) > 2 and file_path[2] == ":":
            file_path = file_path[1:]

        try:
            from .image_extractor import extract_images, format_images_as_markdown

            # Extract images from document
            images = extract_images(file_path)

            if images:
                max_images = int(os.getenv("MARKITDOWN_MAX_IMAGES", "999"))
                print(f"[MarkItDown MCP] Extracted {len(images)} images from document")

                # Format images as Markdown
                images_markdown = format_images_as_markdown(images, max_images=max_images)

                # Append to output
                return result.markdown + "\n\n---\n## Document Images\n" + images_markdown

        except Exception as e:
            print(f"[MarkItDown MCP] Warning: Image extraction failed: {e}")

    return result.markdown


def check_plugins_enabled() -> bool:
    return os.getenv("MARKITDOWN_ENABLE_PLUGINS", "false").strip().lower() in (
        "true",
        "1",
        "yes",
    )


def create_starlette_app(mcp_server: Server, *, debug: bool = False) -> Starlette:
    sse = SseServerTransport("/messages/")
    session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        event_store=None,
        json_response=True,
        stateless=True,
    )

    async def handle_sse(request: Request) -> None:
        async with sse.connect_sse(
            request.scope,
            request.receive,
            request._send,
        ) as (read_stream, write_stream):
            await mcp_server.run(
                read_stream,
                write_stream,
                mcp_server.create_initialization_options(),
            )

    async def handle_streamable_http(
        scope: Scope, receive: Receive, send: Send
    ) -> None:
        await session_manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        """Context manager for session manager."""
        async with session_manager.run():
            print("Application started with StreamableHTTP session manager!")
            try:
                yield
            finally:
                print("Application shutting down...")

    return Starlette(
        debug=debug,
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/mcp", app=handle_streamable_http),
            Mount("/messages/", app=sse.handle_post_message),
        ],
        lifespan=lifespan,
    )


# Main entry point
def main():
    import argparse

    mcp_server = mcp._mcp_server

    parser = argparse.ArgumentParser(description="Run a MarkItDown MCP server")

    parser.add_argument(
        "--http",
        action="store_true",
        help="Run the server with Streamable HTTP and SSE transport rather than STDIO (default: False)",
    )
    parser.add_argument(
        "--sse",
        action="store_true",
        help="(Deprecated) An alias for --http (default: False)",
    )
    parser.add_argument(
        "--host", default=None, help="Host to bind to (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=None, help="Port to listen on (default: 3001)"
    )
    args = parser.parse_args()

    use_http = args.http or args.sse

    if not use_http and (args.host or args.port):
        parser.error(
            "Host and port arguments are only valid when using streamable HTTP or SSE transport (see: --http)."
        )
        sys.exit(1)

    if use_http:
        host = args.host if args.host else "127.0.0.1"
        if args.host and args.host not in ("127.0.0.1", "localhost"):
            print(
                "\n"
                "WARNING: The server is being bound to a non-localhost interface "
                f"({host}).\n"
                "This exposes the server to other machines on the network or Internet.\n"
                "The server has NO authentication and runs with your user's privileges.\n"
                "Any process or user that can reach this interface can read files and\n"
                "fetch network resources accessible to this user.\n"
                "Only proceed if you understand the security implications.\n",
                file=sys.stderr,
            )
        starlette_app = create_starlette_app(mcp_server, debug=True)
        uvicorn.run(
            starlette_app,
            host=host,
            port=args.port if args.port else 3001,
        )
    else:
        mcp.run()


if __name__ == "__main__":
    main()
