from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
import time

from tools import (
    pdf_protect,
    pdf_compress,
    pdf_extract_text,
    image_compress,
    text_analysis,
    spreadsheet,
)

app = FastAPI(title="I Love Tools backend")

ALLOWED_ORIGINS = [
    "https://ilovetools.pro",
    "https://silent5441.github.io",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok", "time": int(time.time())}


@app.post("/api/pdf/protect")
async def protect_pdf(pdf: UploadFile = File(...), password: str = Form(...)):
    if not password:
        raise HTTPException(status_code=400, detail="Password is required")
    data = await pdf.read()
    out = pdf_protect.protect(data, password)
    return Response(
        content=out,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="protected.pdf"'},
    )


@app.post("/api/pdf/compress")
async def compress_pdf(pdf: UploadFile = File(...)):
    data = await pdf.read()
    out, note = pdf_compress.compress(data)
    return Response(
        content=out,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="compressed.pdf"'},
        # note returned in a header (Vercel free tier streams body only)
    )


@app.post("/api/pdf/extract-text")
async def extract_text(pdf: UploadFile = File(...)):
    data = await pdf.read()
    out = pdf_extract_text.extract(data)
    return {"text": out}


@app.post("/api/image/compress")
async def compress_image(image: UploadFile = File(...), quality: int = Form(70)):
    data = await image.read()
    out, mime = image_compress.compress(data, quality)
    return Response(content=out, media_type=mime, headers={"Content-Disposition": 'attachment; filename="compressed-image"'}, )


@app.post("/api/image/resize")
async def resize_image(
    image: UploadFile = File(...), width: int = Form(-1), height: int = Form(-1)
):
    if width < 0 and height < 0:
        raise HTTPException(status_code=400, detail="Provide width or height")
    data = await image.read()
    out, mime = image_compress.resize(data, width, height)
    return Response(content=out, media_type=mime, headers={"Content-Disposition": 'attachment; filename="resized-image"'})


@app.post("/api/image/convert")
async def convert_image(
    image: UploadFile = File(...), format: str = Form("JPEG"), quality: int = Form(90)
):
    data = await image.read()
    out, mime = image_compress.convert(data, format, quality)
    ext = (format or "JPEG").lower().lstrip(".")
    return Response(
        content=out,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="converted-image.{ext}"'},
    )


@app.post("/api/spreadsheet/xlsx-to-csv")
async def xlsx_to_csv(xlsx: UploadFile = File(...)):
    data = await xlsx.read()
    out = spreadsheet.xlsx_to_csv(data)
    return Response(
        content=out,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="converted.csv"'},
    )


@app.post("/api/spreadsheet/csv-to-xlsx")
async def csv_to_xlsx(csv_file: UploadFile = File(...)):
    data = await csv_file.read()
    out = spreadsheet.csv_to_xlsx(data)
    return Response(
        content=out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="converted.xlsx"'},
    )


class AnalysisBody(BaseModel):
    text: str = ""


@app.post("/api/text/analyze")
def analyze(body: AnalysisBody):
    return text_analysis.analyze(body.text)