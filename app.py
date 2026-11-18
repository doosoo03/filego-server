
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
    item.strip()
    for item in os.environ.get(
        "ALLOWED_ORIGINS", ""
    ).split(",")
    if item.strip()
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
            "PDF to DOCX"
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

    # 기존 FileGo는 to 값을 보내지 않으므로
    # 기본 출력 형식을 PDF로 유지합니다.
    target = request.form.get(
        "to", "pdf"
    ).lower()

    supported = {
        (".docx", "pdf"),
        (".pptx", "pdf"),
        (".pdf", "docx")
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
            if extension == ".pdf":
                # PDF -> Word
                from pdf2docx import Converter

                converter = Converter(
                    str(input_file)
                )

                try:
                    converter.convert(
                        str(output_file)
                    )
                finally:
                    converter.close()

            else:
                # Word/PPT -> PDF
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

                output_file = (
                    output_dir / "input.pdf"
                )

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
                "error":
                "파일 변환에 실패했습니다. "
                "다른 파일로 시도해 주세요."
            }), 500

    if target == "pdf":
        mime_type = "application/pdf"
    else:
        mime_type = (
            "application/vnd.openxmlformats-"
            "officedocument.wordprocessingml.document"
        )

    return send_file(
        BytesIO(result_data),
        mimetype=mime_type,
        as_attachment=True,
        download_name=(
            "filego-converted." + target
        )
    )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get("PORT", 10000)
        )
    )
