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


def ensure_directory_exists(directory_path: Path) -> None:
    """Create the directory if it does not exist."""
    directory_path.mkdir(parents=True, exist_ok=True)


def save_uploaded_files(files: List, base_backup_dir: Path) -> Tuple[Path, List[Path]]:
    """Save uploaded files into a timestamped subfolder under the backup directory.

    Returns the path to the created folder and list of saved file paths.
    """
    timestamp_folder = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = base_backup_dir / timestamp_folder
    ensure_directory_exists(backup_dir)

    saved_paths: List[Path] = []
    for uploaded in files:
        safe_name = os.path.basename(uploaded.name).replace("..", "_")
        target_path = backup_dir / safe_name
        with open(target_path, "wb") as f:
            f.write(uploaded.getbuffer())
        saved_paths.append(target_path)

    return backup_dir, saved_paths


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
    attachment_paths: List[Path],
) -> None:
    """Send email through Mailgun with optional attachments."""
    url = f"https://api.mailgun.net/v3/{domain}/messages"
    data = {
        "from": sender,
        "to": recipient,
        "subject": subject,
        "text": text,
    }

    files = []
    try:
        for p in attachment_paths:
            mime_type = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
            files.append(("attachment", (p.name, open(p, "rb"), mime_type)))
        resp = requests.post(url, auth=("api", api_key), data=data, files=files, timeout=60)
        if resp.status_code >= 400:
            raise RuntimeError(f"Mailgun error {resp.status_code}: {resp.text}")
    finally:
        for _, (_, fh, _) in files:
            try:
                fh.close()
            except Exception:
                pass


def main() -> None:
    st.set_page_config(page_title="SS-SEND: Image Mailer", page_icon="📧", layout="centered")
    st.title("📧 SS-SEND: Send Images via Email")
    st.caption("Upload one or more images, send via Mailgun, with local backup and optional remote hosting.")

    # Resolve defaults and env
    app_dir = Path(__file__).parent.resolve()
    default_backup_dir = app_dir / "backups"

    MAILGUN_API_KEY = get_env_any(["MAILGUN_API_KEY"]) 
    MAILGUN_DOMAIN = get_env_any(["MAILGUN_DOMAIN"]) 
    MAILGUN_SENDER = get_env_any(["MAILGUN_SENDER", "MAILGUN_FROM", "MAILGUN_SENDER_EMAIL"]) 

    CLOUDINARY_CLOUD_NAME = get_env_any(["CLOUDINARY_CLOUD_NAME", "CLOUD_NAME", "CLOUDINARY_CLOUD"]) 
    CLOUDINARY_API_KEY = get_env_any(["CLOUDINARY_API_KEY", "CLOUD_API_KEY", "API_KEY"]) 
    CLOUDINARY_API_SECRET = get_env_any(["CLOUDINARY_API_SECRET", "CLOUD_API_SECRET", "API_SECRET"]) 
    CLOUDINARY_FOLDER = get_env_any(["CLOUDINARY_FOLDER", "CLOUD_FOLDER", "FOLDER"]) 

    IMGBB_API_KEY = get_env_any(["IMGBB_API_KEY", "IMG_BB_API_KEY"]) 

    with st.sidebar:
        st.header("Environment status")
        mg_ready = all([MAILGUN_API_KEY, MAILGUN_DOMAIN, MAILGUN_SENDER])
        st.write(f"Mailgun configured: {'✅' if mg_ready else '❌'}")
        cloudinary_ready = all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET])
        imgbb_ready = bool(IMGBB_API_KEY)
        st.write(f"Cloudinary: {'✅' if cloudinary_ready else '❌'} | imgbb: {'✅' if imgbb_ready else '❌'}")

        with st.expander("Troubleshooting"):
            st.write(f".env detected at: {DOTENV_PATH or 'not found'}")
            st.write(f"CLOUDINARY_CLOUD_NAME set: {bool(CLOUDINARY_CLOUD_NAME)}")
            st.write(f"CLOUDINARY_API_KEY set: {bool(CLOUDINARY_API_KEY)}")
            st.write(f"CLOUDINARY_API_SECRET set: {bool(CLOUDINARY_API_SECRET)}")
            st.write(f"CLOUDINARY_FOLDER set: {bool(CLOUDINARY_FOLDER)}")

        st.divider()
        st.header("Backup & Hosting Settings")
        backup_base = st.text_input("Backup folder", value=str(default_backup_dir))
        auto_open_folder = st.toggle("Open backup folder after save", value=False)
        enable_remote_hosting = st.toggle("Upload to image host (if configured)", value=cloudinary_ready or imgbb_ready)
        preferred_host = st.selectbox(
            "Preferred host",
            options=[opt for opt, ok in [("cloudinary", cloudinary_ready), ("imgbb", imgbb_ready)] if ok] or ["none"],
            index=0,
        )
        cloudinary_folder_input = ""
        if cloudinary_ready:
            cloudinary_folder_input = st.text_input("Cloudinary folder (optional)", value=CLOUDINARY_FOLDER, help="e.g., 'ss-send/uploads'")

    uploaded_files = st.file_uploader(
        "Upload image file(s)",
        type=["png", "jpg", "jpeg", "gif", "bmp", "tiff"],
        accept_multiple_files=True,
    )

    recipient_email = st.text_input("Recipient email")
    default_from_display = MAILGUN_SENDER or "sender@example.com"
    from_display = st.text_input("Sender (from)", value=default_from_display, help="Uses MAILGUN_SENDER by default")
    subject = st.text_input("Subject", value="Your images from SS-SEND")
    body = st.text_area("Message", value="Please find the attached images.")

    if uploaded_files:
        st.subheader("Preview")
        cols = st.columns(3)
        for idx, uf in enumerate(uploaded_files):
            with cols[idx % 3]:
                st.image(uf, caption=uf.name, use_column_width=True)

    send_clicked = st.button("Send with Mailgun and Backup", type="primary")

    if send_clicked:
        # Validation
        if not uploaded_files:
            st.error("Please upload at least one image.")
            return
        if not recipient_email:
            st.error("Please provide the recipient email address.")
            return
        if not mg_ready:
            st.error("Mailgun is not configured. Please set MAILGUN_API_KEY, MAILGUN_DOMAIN, MAILGUN_SENDER in your .env.")
            return

        base_dir = Path(backup_base).expanduser().resolve()
        try:
            with st.spinner("Saving backup, uploading (optional), and sending email..."):
                # Save backup
                backup_folder, saved_paths = save_uploaded_files(uploaded_files, base_dir)

                # Optional remote upload
                remote_urls: List[str] = []
                if enable_remote_hosting and saved_paths:
                    for path in saved_paths:
                        url: Optional[str] = None
                        if preferred_host == "cloudinary" and cloudinary_ready:
                            url = upload_to_cloudinary(
                                path,
                                CLOUDINARY_CLOUD_NAME,
                                CLOUDINARY_API_KEY,
                                CLOUDINARY_API_SECRET,
                                folder=cloudinary_folder_input or None,
                            )
                        elif preferred_host == "imgbb" and imgbb_ready:
                            url = upload_to_imgbb(path, IMGBB_API_KEY)
                        if url:
                            remote_urls.append(url)

                # Compose email body
                url_block = "\n\nLinks:\n" + "\n".join(remote_urls) if remote_urls else ""
                email_body = f"{body}{url_block}\n\nTotal attachments: {len(saved_paths)}\nBackup folder: {backup_folder}"

                # Send via Mailgun with attachments
                send_via_mailgun(
                    api_key=MAILGUN_API_KEY,
                    domain=MAILGUN_DOMAIN,
                    sender=from_display or MAILGUN_SENDER,
                    recipient=recipient_email,
                    subject=subject,
                    text=email_body,
                    attachment_paths=saved_paths,
                )
        except requests.RequestException as re:
            st.error(f"Network error: {re}")
            return
        except Exception as e:
            st.error(f"Failed to complete operation: {e}")
            return

        st.success("Email sent, backup saved, and uploads (if enabled) completed!")
        st.info(f"Backup folder: {backup_folder}")

        if enable_remote_hosting and not remote_urls:
            st.warning("Remote hosting was enabled but no URLs were returned. Check your hosting configuration.")

        if auto_open_folder:
            try:
                if os.name == "nt":
                    os.startfile(str(backup_folder))  # type: ignore[attr-defined]
                elif os.name == "posix":
                    os.system(f"xdg-open '{backup_folder}' >/dev/null 2>&1 &")
            except Exception:
                pass


if __name__ == "__main__":
    main() 