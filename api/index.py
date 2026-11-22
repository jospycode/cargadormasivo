import os
import io
import re
import zipfile
import traceback
import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import cloudinary
import cloudinary.uploader
import cloudinary.api

app = FastAPI(root_path="/api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")

HAS_CLOUDINARY = bool(CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET)

if HAS_CLOUDINARY:
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
    )

def sanitize_string(val: str, fallback: str = "archivo") -> str:
    clean = re.sub(r'[^a-zA-Z0-9._-]', '_', os.path.basename(val or ''))
    return clean or fallback

@app.post("/api/upload")
@app.post("/upload")
async def upload_file(file: UploadFile = File(...), batchId: str = Form("default")):
    if not HAS_CLOUDINARY:
        print("ERROR: HAS_CLOUDINARY es False. Revisa las variables de entorno.")
        raise HTTPException(status_code=500, detail="Faltan credenciales de Cloudinary en el servidor.")

    clean_batch_id = sanitize_string(batchId, "default")
    clean_name = sanitize_string(file.filename, "archivo")

    try:
        contents = await file.read()
        if not contents:
            raise HTTPException(status_code=400, detail="El archivo está vacío.")

        upload_result = cloudinary.uploader.upload(
            io.BytesIO(contents),
            folder=f"compartidor_imagenes/{clean_batch_id}",
            resource_type="auto",
            use_filename=True,
            unique_filename=True,
        )
        return {
            "ok": True,
            "name": clean_name,
            "url": upload_result.get("secure_url"),
            "publicId": upload_result.get("public_id"),
            "batchId": clean_batch_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error al subir a Cloudinary: {str(e)}")

def fetch_cloudinary_batch_files(batch_id: str):
    if not HAS_CLOUDINARY or not batch_id:
        return []

    collected = []
    prefix = f"compartidor_imagenes/{batch_id}/"

    for r_type in ["image", "video", "raw"]:
        try:
            res = cloudinary.api.resources(
                type="upload",
                resource_type=r_type,
                prefix=prefix,
                max_results=100,
            )
            for asset in res.get("resources", []):
                secure_url = asset.get("secure_url")
                public_id = asset.get("public_id", "")
                if not secure_url or "/zips/" in public_id:
                    continue

                base_name = os.path.basename(public_id)
                fmt = asset.get("format", "")
                ext = f".{fmt}" if fmt else ""
                name = base_name if base_name.endswith(ext) else f"{base_name}{ext}"
                collected.append({"url": secure_url, "name": sanitize_string(name)})
        except Exception:
            pass

    return collected

@app.post("/api/create-zip")
@app.post("/create-zip")
async def create_zip(payload: dict):
    batch_id = sanitize_string(payload.get("batchId"), "batch_default")
    files = payload.get("files", [])

    remote_files = []
    for f in files:
        if isinstance(f, str) and f.startswith("http"):
            remote_files.append({"url": f.strip(), "name": "archivo"})
        elif isinstance(f, dict) and f.get("url"):
            remote_files.append({
                "url": f["url"].strip(),
                "name": sanitize_string(f.get("name"), "archivo"),
            })

    if not remote_files:
        remote_files = fetch_cloudinary_batch_files(batch_id)

    if not remote_files:
        raise HTTPException(
            status_code=404,
            detail="No se encontraron archivos en Cloudinary para crear el ZIP.",
        )

    try:
        zip_buffer = io.BytesIO()
        used_names = set()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for idx, item in enumerate(remote_files):
                resp = requests.get(item["url"])
                if resp.status_code != 200:
                    continue

                raw_name = item["name"]
                unique_name = raw_name
                while unique_name in used_names:
                    base, ext = os.path.splitext(raw_name)
                    unique_name = f"{base}_{idx + 1}{ext}"
                used_names.add(unique_name)

                zip_file.writestr(unique_name, resp.content)

        zip_filename = f"{batch_id}.zip"
        zip_buffer.seek(0)

        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{zip_filename}"'
            },
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error al generar ZIP: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.index:app", host="0.0.0.0", port=4000, reload=True)