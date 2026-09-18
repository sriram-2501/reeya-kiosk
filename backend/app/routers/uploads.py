from fastapi import APIRouter, HTTPException, Request, Response

from app.models.schemas import PresignRequest, PresignResponse
from app.services.storage import build_public_url, generate_presigned_put_url, save_upload, verify_signature

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("/presign", response_model=PresignResponse)
def presign_upload(payload: PresignRequest) -> PresignResponse:
    """Returns a short-lived signed upload URL for a direct client upload.

    Flow: client calls this endpoint -> PUTs the file bytes straight to
    `upload_url` (this backend's own PUT /uploads/put/{object_key} below,
    not a third-party object store) -> then calls POST /image-search with
    `public_url`.
    """
    upload_url, object_key = generate_presigned_put_url(
        user_id=str(payload.user_id),
        filename=payload.filename,
        content_type=payload.content_type,
    )
    return PresignResponse(
        upload_url=upload_url,
        object_key=object_key,
        public_url=build_public_url(object_key),
    )


@router.put("/put/{object_key:path}")
async def put_upload(object_key: str, request: Request, expires: int, sig: str) -> Response:
    """The actual upload target for a presigned URL from /uploads/presign
    above. `expires`/`sig` are the same signature scheme generate_presigned_put_url
    embeds in the URL's query string — verified here exactly like a real S3
    presigned URL would be verified by AWS itself, just homegrown (see
    app/services/storage.py for why there's no third-party object store
    backing this instead).
    """
    if not verify_signature(object_key, expires, sig):
        raise HTTPException(status_code=403, detail="Invalid or expired upload URL")

    data = await request.body()
    save_upload(object_key, data)
    return Response(status_code=200)
