
import os
import tempfile
import subprocess
from io import BytesIO
from pathlib import Path

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename

app = Flask(__name__)

app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024

origins = [
    s.strip()
    for s in os.environ.get(
        "ALLOWED_ORIGINS", ""
    ).split(",")
    if s.strip()
]

CORS(
    app,
    resources={
        r"/api/*": {
            "origins": origins
        }
    }
)


@app.get("/")
def home():
    return jsonify({
        "service": "FileGo Conversion Server",
        "status": "running",
        "supported": [
            "DOCX to PDF",
            "PPTX to PDF",
            "PDF to DOCX",
            "PDF to PPTX (image slides)"
        ]
    })


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.errorhandler(413)
def too_large(error):
    return jsonify({
        "error": "파일 크기는 최대 30MB입니다."
    }), 413


@app.post("/api/convert")
def convert():
    uploaded = request.files.get("file")

    if uploaded is None or not uploaded.filename:
        return jsonify({
            "error": "파일을 선택해 주세요."
        }), 400

    filename = secure_filename(uploaded.filename)
    extension = Path(filename).suffix.lower()

    # 기존 클라이언트 호환성을 위해 기본값은 pdf
    target = request.form.get("to", "pdf").lower()

    supported = {
        (".docx", "pdf"),
        (".pptx", "pdf"),
        (".pdf", "docx"),
        (".pdf", "pptx")
    }

    if (extension, target) not in supported:
        return jsonify({
            "error": "지원하지 않는 변환입니다."
        }), 400

    with tempfile.TemporaryDirectory(
        prefix="filego_"
    ) as temp:

        root = Path(temp)

        input_file = root / (
            "input" + extension
        )

        output_file = root / (
            "input." + target
        )

        uploaded.save(input_file)

        try:
            # PDF → Word
            if extension == ".pdf" and target == "docx":
                from pdf2docx import Converter

                converter = Converter(str(input_file))

                try:
                    converter.convert(
                        str(output_file)
                    )
                finally:
                    converter.close()

            # PDF → PPT (페이지를 이미지 슬라이드로)
            elif extension == ".pdf" and target == "pptx":
                import fitz
                from pptx import Presentation
                from pptx.util import Inches

                pdf = fitz.open(str(input_file))

                try:
                    if pdf.is_encrypted:
                        return jsonify({
                            "error": "암호화된 PDF는 지원하지 않습니다."
                        }), 400

                    if pdf.page_count == 0 or pdf.page_count > 30:
                        return jsonify({
                            "error": "PDF → PPT는 1~30페이지를 지원합니다."
                        }), 400

                    presentation = Presentation()

                    presentation.slide_width = Inches(10)
                    presentation.slide_height = Inches(7.5)

                    blank_layout = presentation.slide_layouts[6]

                    for page in pdf:
                        pix = page.get_pixmap(
                            matrix=fitz.Matrix(1.5, 1.5),
                            alpha=False
                        )

                        slide = presentation.slides.add_slide(
                            blank_layout
                        )

                        page_ratio = pix.width / pix.height

                        slide_ratio = (
                            presentation.slide_width /
                            presentation.slide_height
                        )

                        if page_ratio >= slide_ratio:
                            width = presentation.slide_width
                            height = int(width / page_ratio)
                            left = 0
                            top = int(
                                (presentation.slide_height - height) / 2
                            )
                        else:
                            height = presentation.slide_height
                            width = int(height * page_ratio)
                            left = int(
                                (presentation.slide_width - width) / 2
                            )
                            top = 0

                        slide.shapes.add_picture(
                            BytesIO(pix.tobytes("png")),
                            left,
                            top,
                            width=width,
                            height=height
                        )

                    presentation.save(
                        str(output_file)
                    )
                finally:
                    pdf.close()

            # Word/PPT → PDF
            else:
                output_dir = root / "output"
                output_dir.mkdir()

                command = [
                    "libreoffice",
                    "-env:UserInstallation=file://" +
                    str(root / "lo-profile"),
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(output_dir),
                    str(input_file)
                ]

                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=90,
                    check=False
                )

                output_file = output_dir / "input.pdf"

                if result.returncode != 0:
                    raise RuntimeError(
                        "LibreOffice conversion failed"
                    )

            if (
                not output_file.exists()
                or output_file.stat().st_size == 0
            ):
                raise RuntimeError(
                    "Output file was not created"
                )

            result_data = output_file.read_bytes()

        except Exception:
            app.logger.exception(
                "File conversion failed"
            )

            return jsonify({
                "error": "파일 변환에 실패했습니다."
            }), 500

    mime_types = {
        "pdf": "application/pdf",
        "docx": (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        "pptx": (
            "application/vnd.openxmlformats-officedocument."
            "presentationml.presentation"
        )
    }

    return send_file(
        BytesIO(result_data),
        mimetype=mime_types[target],
        as_attachment=True,
        download_name="filego-converted." + target
    )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
