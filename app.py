
import os
import tempfile
import subprocess
import shutil
from pathlib import Path

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename

app = Flask(__name__)

# 업로드 최대 크기: 30MB
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024

# 개발 단계에서만 사용합니다.
# 실제 FileGo 주소가 정해지면 허용 주소를 제한합니다.
allowed_origins = os.environ.get("ALLOWED_ORIGINS", "").split(",")

CORS(
    app,
    resources={
        r"/api/*": {
            "origins": (
                [x.strip() for x in allowed_origins if x.strip()]
                if any(x.strip() for x in allowed_origins)
                else []
            )
        }
    },
)

ALLOWED_EXTENSIONS = {"docx", "pptx"}


@app.get("/")
def home():
    return jsonify({
        "service": "FileGo Conversion Server",
        "status": "running",
        "supported": [
            "DOCX to PDF",
            "PPTX to PDF"
        ]
    })


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.errorhandler(413)
def file_too_large(error):
    return jsonify({
        "error": "파일 크기는 최대 30MB입니다."
    }), 413


@app.post("/api/convert")
def convert():
    uploaded = request.files.get("file")

    if uploaded is None:
        return jsonify({
            "error": "파일이 업로드되지 않았습니다."
        }), 400

    if not uploaded.filename:
        return jsonify({
            "error": "파일 이름이 없습니다."
        }), 400

    original_name = secure_filename(uploaded.filename)

    if not original_name:
        return jsonify({
            "error": "올바르지 않은 파일 이름입니다."
        }), 400

    extension = Path(original_name).suffix.lower().lstrip(".")

    if extension not in ALLOWED_EXTENSIONS:
        return jsonify({
            "error": "DOCX 또는 PPTX 파일만 지원합니다."
        }), 400

    with tempfile.TemporaryDirectory(prefix="filego_") as workdir:
        input_dir = Path(workdir) / "input"
        output_dir = Path(workdir) / "output"
        profile_dir = Path(workdir) / "lo-profile"

        input_dir.mkdir()
        output_dir.mkdir()

        # 서버 내부에서는 고정된 파일명을 사용합니다.
        input_path = input_dir / f"document.{extension}"
        output_path = output_dir / "document.pdf"

        uploaded.save(input_path)

        command = [
            "libreoffice",
            "-env:UserInstallation=file://" + str(profile_dir),
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_dir),
            str(input_path),
        ]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return jsonify({
                "error": "변환 시간이 초과되었습니다."
            }), 504
        except OSError:
            app.logger.exception("LibreOffice execution failed")
            return jsonify({
                "error": "변환 프로그램을 실행하지 못했습니다."
            }), 500

        if result.returncode != 0 or not output_path.exists():
            app.logger.error(
                "Conversion failed: %s %s",
                result.stdout,
                result.stderr,
            )
            return jsonify({
                "error": "파일 변환에 실패했습니다."
            }), 500

        # 임시 폴더가 삭제되기 전에 결과를 메모리로 읽습니다.
        pdf_data = output_path.read_bytes()

    from io import BytesIO

    return send_file(
        BytesIO(pdf_data),
        mimetype="application/pdf",
        as_attachment=True,
        download_name="filego-converted.pdf",
    )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
