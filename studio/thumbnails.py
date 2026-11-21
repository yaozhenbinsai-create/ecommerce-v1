"""Disposable thumbnail cache. Full source assets remain immutable."""
from io import BytesIO
from pathlib import Path
import os
import uuid

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from . import db, domain as d

router = APIRouter()
SIZES = {160, 320, 640}


def build(asset, size):
    if size not in SIZES:
        raise d.Problem('缩略图尺寸只支持 160、320、640 像素')
    root = (db.DATA / 'temporary' / 'thumbnails').resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Registry hashes and sizes are validated even though the path is server-owned.
    digest = str(asset['sha256'])
    if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise d.Problem('资产哈希无效')
    path = root / f'{digest}-{size}-v1.jpg'
    if path.is_file():
        return path
    try:
        with Image.open(asset['path']) as source:
            source.draft('RGB', (size, size))
            frame = ImageOps.exif_transpose(source)
            frame.thumbnail((size, size), Image.Resampling.LANCZOS)
            if frame.mode in {'RGBA', 'LA'} or 'transparency' in frame.info:
                rgba = frame.convert('RGBA')
                output = Image.new('RGB', rgba.size, '#ffffff')
                output.paste(rgba, mask=rgba.getchannel('A'))
            else:
                output = frame.convert('RGB')
            raw = BytesIO()
            output.save(raw, 'JPEG', quality=82, optimize=True)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise d.Problem('此资产不能读取为图片缩略图') from None
    temporary = root / (path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('wb') as stream:
            stream.write(raw.getvalue())
            stream.flush()
            os.fsync(stream.fileno())
        # Equivalent concurrent cache results are interchangeable; no DB identity is changed.
        try:
            temporary.replace(path)
        except PermissionError:
            if not path.is_file():
                raise
    finally:
        temporary.unlink(missing_ok=True)
    return path


@router.get('/api/assets/{asset_id}/thumbnail')
def thumbnail(asset_id: int, request: Request, size: int = Query(320)):
    asset = d.get_asset(asset_id)
    if size not in SIZES:
        raise d.Problem('缩略图尺寸只支持 160、320、640 像素')
    etag = f'"{asset["sha256"]}-{size}-v1"'
    headers = {'ETag': etag, 'Cache-Control': 'private, max-age=86400'}
    if request.headers.get('if-none-match') == etag:
        return Response(status_code=304, headers=headers)
    return FileResponse(build(asset, size), media_type='image/jpeg', headers=headers)
