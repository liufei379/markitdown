"""
Tesseract OCR for MarkItDown MCP
Extracts text from images using Tesseract OCR (local engine)
"""

import os
import subprocess
from pathlib import Path
from typing import Optional


class TesseractOCR:
    """Tesseract OCR handler with auto-detection and installation guide"""

    def __init__(self):
        self.tesseract_cmd = self._find_tesseract()
        self.enabled = self.tesseract_cmd is not None

        if not self.enabled:
            self._print_install_guide()
        else:
            print(f"[Tesseract OCR] Found at: {self.tesseract_cmd}")
            self._init_pytesseract()

    def _find_tesseract(self) -> Optional[str]:
        """
        Find Tesseract installation path

        Returns:
            Path to tesseract executable or None if not found
        """
        # Common installation paths
        common_paths = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            "/usr/bin/tesseract",  # Linux
            "/usr/local/bin/tesseract",  # Mac (Homebrew)
            "/opt/homebrew/bin/tesseract",  # Mac (Apple Silicon)
        ]

        # Check common paths first
        for path in common_paths:
            if os.path.exists(path):
                return path

        # Check if tesseract is in PATH
        try:
            result = subprocess.run(
                ["tesseract", "--version"],
                capture_output=True,
                timeout=2
            )
            if result.returncode == 0:
                return "tesseract"  # Available in PATH
        except:
            pass

        return None

    def _init_pytesseract(self):
        """Initialize pytesseract with detected path"""
        try:
            import pytesseract
            if self.tesseract_cmd != "tesseract":
                pytesseract.pytesseract.tesseract_cmd = self.tesseract_cmd
            print("[Tesseract OCR] Initialized successfully")
        except ImportError:
            print("[Tesseract OCR] Warning: pytesseract not installed")
            print("[Tesseract OCR] Install: pip install pytesseract")
            self.enabled = False

    def _print_install_guide(self):
        """Print clear installation guide for Tesseract"""
        print("\n" + "=" * 70)
        print("Tesseract OCR not found!")
        print("=" * 70)
        print("\nTo enable image text extraction, install Tesseract OCR:\n")

        print("  Windows:")
        print("    winget install --id UB-Mannheim.TesseractOCR\n")

        print("  Mac:")
        print("    brew install tesseract\n")

        print("  Linux (Ubuntu/Debian):")
        print("    sudo apt install tesseract-ocr\n")

        print("  Linux (Fedora/RHEL):")
        print("    sudo dnf install tesseract\n")

        print("-" * 70)
        print("After installation:")
        print("  1. Restart Claude Code CLI")
        print("  2. Image OCR will work automatically")
        print("-" * 70)
        print("\nWithout Tesseract, images will be returned as compressed grayscale")
        print("for Claude to analyze visually.")
        print("=" * 70 + "\n")

    def extract_text_from_image(self, img_bytes: bytes, lang: str = 'eng') -> Optional[str]:
        """
        Extract text from image using Tesseract OCR

        Args:
            img_bytes: Raw image bytes
            lang: Language code (eng, chi_sim, chi_tra, etc.)

        Returns:
            Extracted text or None if Tesseract not available
        """
        if not self.enabled:
            return None

        try:
            import pytesseract
            from PIL import Image
            import io

            # Open image
            img = Image.open(io.BytesIO(img_bytes))

            # Perform OCR
            text = pytesseract.image_to_string(img, lang=lang)

            if text and text.strip():
                print(f"[Tesseract OCR] Extracted {len(text)} characters")
                return text.strip()
            else:
                return ""

        except Exception as e:
            print(f"[Tesseract OCR] Error during OCR: {e}")
            return None

    def is_available(self) -> bool:
        """Check if Tesseract is available"""
        return self.enabled


# Global instance
tesseract_ocr = TesseractOCR()
