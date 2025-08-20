import streamlit as st
import os
import mimetypes
import hashlib
import time
import base64
import requests
from email.utils import formataddr
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional
from dotenv import load_dotenv, find_dotenv


# Load environment variables from nearest .env (robust)
DOTENV_PATH = find_dotenv(usecwd=True)
load_dotenv(DOTENV_PATH, override=True)
# Also try .env in app dir and project root
for extra_env in [Path(__file__).parent / ".env", Path(__file__).resolve().parents[1] / ".env"]:
    try:
        if extra_env.exists():
            load_dotenv(extra_env, override=True)
    except Exception:
        pass


# -------------- Env helpers --------------

def get_env_any(keys: List[str], default: str = "") -> str:
    for key in keys:
        val = os.getenv(key)
        if val is not None and str(val).strip() != "":
            return str(val).strip()
    return default


# -------------- Remote hosting helpers --------------

def _cloudinary_signature(params: dict, api_secret: str) -> str:
    """Create Cloudinary signature from params using API secret.

    Cloudinary requires params (without file) to be sorted by key, joined as key=value pairs with &,
    then append API secret and SHA-1 hash the result.
    """
    filtered = {k: v for k, v in params.items() if v not in (None, "")}
    signature_base = "&".join(f"{k}={filtered[k]}" for k in sorted(filtered.keys())) + api_secret
    return hashlib.sha1(signature_base.encode("utf-8")).hexdigest()


def upload_to_cloudinary(
    file_path: Path,
    cloud_name: str,
    api_key: str,
    api_secret: str,
    folder: Optional[str] = None,
) -> Optional[str]:
    """Upload an image to Cloudinary using a signed upload; return secure URL or None."""
    try:
        timestamp = int(time.time())
        url = f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload"
        params = {"timestamp": str(timestamp)}
        if folder:
            params["folder"] = folder
        signature = _cloudinary_signature(params, api_secret)
        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f, mimetypes.guess_type(str(file_path))[0] or "application/octet-stream")}
            data = {"api_key": api_key, "signature": signature, **params}
            resp = requests.post(url, data=data, files=files, timeout=60)
        if resp.status_code == 200:
            return resp.json().get("secure_url")
        return None
    except Exception:
        return None

# NEW: direct-bytes upload for Streamlit Cloud (no local files)
def upload_bytes_to_cloudinary(
    file_bytes: bytes,
    filename: str,
    cloud_name: str,
    api_key: str,
    api_secret: str,
    folder: Optional[str] = None,
) -> Optional[str]:
    try:
        timestamp = int(time.time())
        url = f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload"
        params = {"timestamp": str(timestamp)}
        if folder:
            params["folder"] = folder
        signature = _cloudinary_signature(params, api_secret)
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        files = {"file": (filename, file_bytes, mime_type)}
        data = {"api_key": api_key, "signature": signature, **params}
        resp = requests.post(url, data=data, files=files, timeout=60)
        if resp.status_code == 200:
            return resp.json().get("secure_url")
        return None
    except Exception:
        return None


def upload_to_imgbb(file_path: Path, api_key: str) -> Optional[str]:
    """Upload an image to imgbb; return URL or None."""
    try:
        url = "https://api.imgbb.com/1/upload"
        with open(file_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        resp = requests.post(url, data={"key": api_key, "image": b64}, timeout=60)
        if resp.status_code == 200 and resp.json().get("success"):
            return resp.json()["data"].get("url")
        return None
    except Exception:
        return None


# -------------- Mail sending via Mailgun --------------

def send_via_mailgun(
    api_key: str,
    domain: str,
    sender: str,
    recipient: str,
    subject: str,
    text: str,
    attachments: List[Tuple[str, bytes, str]],
) -> None:
    """Send email through Mailgun with in-memory attachments."""
    url = f"https://api.mailgun.net/v3/{domain}/messages"
    data = {
        "from": sender,
        "to": recipient,
        "subject": subject,
        "text": text,
    }

    files: List[Tuple[str, Tuple[str, bytes, str]]] = []
    for name, content_bytes, mime_type in attachments:
        files.append(("attachment", (name, content_bytes, mime_type)))

    resp = requests.post(url, auth=("api", api_key), data=data, files=files, timeout=60)
    if resp.status_code >= 400:
        raise RuntimeError(f"Mailgun error {resp.status_code}: {resp.text}")


def main() -> None:
    st.set_page_config(page_title="SS-SEND: Image Mailer", page_icon="📧", layout="centered")
    st.title("📧 SS-SEND: Send Images via Email")
    st.caption("Upload images, they are uploaded to Cloudinary and emailed via Mailgun. No local storage.")

    # Resolve defaults and env
    MAILGUN_API_KEY = get_env_any(["MAILGUN_API_KEY"]) 
    MAILGUN_DOMAIN = get_env_any(["MAILGUN_DOMAIN"]) 
    MAILGUN_SENDER = get_env_any(["MAILGUN_SENDER", "MAILGUN_FROM", "MAILGUN_SENDER_EMAIL"]) 

    CLOUDINARY_CLOUD_NAME = get_env_any(["CLOUDINARY_CLOUD_NAME", "CLOUD_NAME", "CLOUDINARY_CLOUD"]) 
    CLOUDINARY_API_KEY = get_env_any(["CLOUDINARY_API_KEY", "CLOUD_API_KEY", "API_KEY"]) 
    CLOUDINARY_API_SECRET = get_env_any(["CLOUDINARY_API_SECRET", "CLOUD_API_SECRET", "API_SECRET"]) 
    CLOUDINARY_FOLDER = get_env_any(["CLOUDINARY_FOLDER", "CLOUD_FOLDER", "FOLDER"]) 

    # Prepare readiness flags (not displayed)
    mg_ready = all([MAILGUN_API_KEY, MAILGUN_DOMAIN, MAILGUN_SENDER])
    cloudinary_ready = all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET])
    cloudinary_folder_input = CLOUDINARY_FOLDER

    uploaded_files = st.file_uploader(
        "Upload image file(s)",
        type=["png", "jpg", "jpeg", "gif", "bmp", "tiff"],
        accept_multiple_files=True,
    )

    recipient_email = st.text_input("Recipient email")
    subject = st.text_input("Subject", value="Your images from SS-SEND")
    body = st.text_area("Message", value="Please find the attached images.")

    if uploaded_files:
        st.subheader("Preview")
        cols = st.columns(3)
        for idx, uf in enumerate(uploaded_files):
            with cols[idx % 3]:
                st.image(uf, caption=uf.name, use_container_width=True)

    send_clicked = st.button("Upload to Cloudinary and Send Email", type="primary")

    if send_clicked:
        if not uploaded_files:
            st.error("Please upload at least one image.")
            return
        if not recipient_email:
            st.error("Please provide the recipient email address.")
            return
        if not mg_ready:
            st.error("Mailgun is not configured. Please set MAILGUN_API_KEY, MAILGUN_DOMAIN, MAILGUN_SENDER in your .env.")
            return
        if not cloudinary_ready:
            st.error("Cloudinary is not configured. Please set CLOUDINARY_* vars in your .env.")
            return

        try:
            with st.spinner("Uploading to Cloudinary and sending email..."):
                remote_urls: List[str] = []
                attachments: List[Tuple[str, bytes, str]] = []
                for uf in uploaded_files:
                    file_bytes = uf.getbuffer()
                    filename = uf.name
                    url = upload_bytes_to_cloudinary(
                        file_bytes=file_bytes,
                        filename=filename,
                        cloud_name=CLOUDINARY_CLOUD_NAME,
                        api_key=CLOUDINARY_API_KEY,
                        api_secret=CLOUDINARY_API_SECRET,
                        folder=cloudinary_folder_input or None,
                    )
                    if url:
                        remote_urls.append(url)
                    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                    attachments.append((filename, file_bytes, mime_type))

                if len(remote_urls) != len(uploaded_files):
                    st.warning("Some Cloudinary uploads did not return URLs. Check your configuration.")

                url_block = "\n\nLinks:\n" + "\n".join(remote_urls) if remote_urls else ""
                email_body = f"{body}{url_block}\n\nTotal images: {len(uploaded_files)}"

                send_via_mailgun(
                    api_key=MAILGUN_API_KEY,
                    domain=MAILGUN_DOMAIN,
                    sender=MAILGUN_SENDER,
                    recipient=recipient_email,
                    subject=subject,
                    text=email_body,
                    attachments=attachments,
                )
        except requests.RequestException as re:
            st.error(f"Network error: {re}")
            return
        except Exception as e:
            st.error(f"Failed to complete operation: {e}")
            return

        st.success("Uploaded to Cloudinary and emailed successfully!")


if __name__ == "__main__":
    main() 