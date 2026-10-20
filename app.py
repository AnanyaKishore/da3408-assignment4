import io
import threading
import time
import zipfile
from pathlib import PurePosixPath
import pandas as pd
import streamlit as st # streamlit
import torch
from PIL import Image, UnidentifiedImageError
from transformers import BlipForConditionalGeneration, BlipProcessor
import metrics as m # from metrics.py

MODEL_NAME = "Salesforce/blip-image-captioning-base"
APP_VERSION = "1.0.0"
METRICS_PORT = 18000
MAX_NEW_TOKENS = 30
MAX_ZIP_IMAGES = 200 # cap per zip
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # skip single files larger than 20 MB in a zip
TORCH_THREADS = 8 # I have 12 cores in total
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif", ".tif", ".tiff"}

st.set_page_config(page_title="CPU Image Captioning", layout="wide")

@st.cache_resource # setup once as cache_resource runs once per server process and not per rerun
def start_metrics() -> bool:
    m.start_metrics_server(METRICS_PORT)
    return True

@st.cache_resource(show_spinner="Loading BLIP on CPU (first run downloads ~1 GB)...")
def load_model():
    torch.set_num_threads(TORCH_THREADS)
    t0 = time.perf_counter()
    processor = BlipProcessor.from_pretrained(MODEL_NAME)
    model = BlipForConditionalGeneration.from_pretrained(MODEL_NAME)
    model.eval()

    m.MODEL_LOAD_SECONDS.set(time.perf_counter() - t0)
    m.MODEL_LOADED.set(1)
    m.MODEL_PARAMS.set(sum(p.numel() for p in model.parameters()))
    m.TORCH_THREADS.set(torch.get_num_threads())
    m.APP_INFO.info({"version": APP_VERSION, "model": MODEL_NAME, "device": "cpu", "torch_version": torch.__version__})
    return processor, model, threading.Lock()

def client_info() -> tuple[str, str]: # returns (client_ip, session_id) for current browser session
    ip = None
    try:
        ip = st.context.ip_address # None when opened via localhost
    except Exception:
        pass
    if not ip:
        try:
            ip = st.context.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        except Exception:
            ip = ""
    ip = ip or "localhost"
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        ctx = get_script_run_ctx()
        sid = ctx.session_id[:8] if ctx else "unknown"
    except Exception:
        sid = "unknown"
    return ip, sid

# Captioning
def caption_image(img: Image.Image, mode: str, processor, model, lock) -> tuple[str, float]:
    # records metrics per PIL image and returns the caption, seconds
    t0 = time.perf_counter()
    img = img.convert("RGB")
    m.IMAGE_MEGAPIXELS.labels(mode).observe(img.width * img.height / 1e6)
    img.thumbnail((1024, 1024))
    inputs = processor(images=img, return_tensors="pt") # preprocessing
    m.PREPROCESS_SECONDS.labels(mode).observe(time.perf_counter() - t0)

    wait_start = time.perf_counter()
    with lock:
        m.LOCK_WAIT.labels(mode).observe(time.perf_counter() - wait_start) # locking time
        t1 = time.perf_counter()
        with torch.inference_mode():
            out = model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS)
        infer_s = time.perf_counter() - t1 # inference time

    caption = processor.decode(out[0], skip_special_tokens=True).strip()
    # logging metrics
    m.INFERENCE_SECONDS.labels(mode).observe(infer_s)
    m.TOKENS_GENERATED.labels(mode).inc(out.shape[1])
    m.CAPTION_WORDS.labels(mode).observe(len(caption.split()))
    m.LAST_INFERENCE_TS.set_to_current_time()
    return caption, infer_s

def process_one(name: str, data: bytes, mode: str, ip: str, sid: str, res) -> dict:
    # decode and caption one image and count failures
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        m.IMAGES_PROCESSED.labels(mode, "failed").inc()
        m.ERRORS.labels(mode, "bad_image").inc()
        return {"file": name, "caption": "", "inference_s": None, "status": "failed: unreadable image"}

    try:
        caption, secs = caption_image(img, mode, *res)
    except Exception as e: # we want the app to stay alive
        m.IMAGES_PROCESSED.labels(mode, "failed").inc()
        m.ERRORS.labels(mode, "inference_error").inc()
        return {"file": name, "caption": "", "inference_s": None, "status": f"failed: {type(e).__name__}"}

    m.IMAGES_PROCESSED.labels(mode, "success").inc()
    m.CLIENT_IMAGES.labels(mode, ip, sid).inc()
    return {"file": name, "caption": caption, "inference_s": round(secs, 3), "status": "ok"}

# Single mode
def run_single(uploaded, ip, sid, res):
    data = uploaded.getvalue()
    m.REQUESTS.labels("single", ip, sid).inc()
    m.UPLOAD_SIZE.labels("single").observe(len(data))
    m.BYTES_RECEIVED.labels("single").inc(len(data))

    with m.ACTIVE_REQUESTS.labels("single").track_inprogress(), m.REQUEST_SECONDS.labels("single").time():
        with st.spinner("Generating caption..."):
            row = process_one(uploaded.name, data, "single", ip, sid, res)

    col1, col2 = st.columns([1, 1])
    if row["status"] == "ok":
        col1.image(data)
        col2.success(f"**Caption:** {row['caption']}")
        col2.caption(f"Inference time: {row['inference_s']} s")
    else:
        st.error(f"Could not caption this file ({row['status']}).")

# Bulk / ZIP mode
def list_zip_images(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    entries = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        p = PurePosixPath(info.filename)
        if p.name.startswith(".") or "__MACOSX" in p.parts:
            m.SKIPPED_FILES.labels("hidden").inc()
        elif p.suffix.lower() not in IMAGE_EXTS:
            m.SKIPPED_FILES.labels("not_image").inc()
        elif info.file_size > MAX_IMAGE_BYTES:
            m.SKIPPED_FILES.labels("too_large").inc()
        else:
            entries.append(info)
    return entries

def run_bulk(uploaded, ip, sid, res):
    data = uploaded.getvalue()
    m.REQUESTS.labels("bulk", ip, sid).inc()
    m.UPLOAD_SIZE.labels("bulk").observe(len(data))
    m.BYTES_RECEIVED.labels("bulk").inc(len(data))

    with m.ACTIVE_REQUESTS.labels("bulk").track_inprogress(), m.REQUEST_SECONDS.labels("bulk").time():
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            m.ERRORS.labels("bulk", "bad_zip").inc()
            st.error("That file is not a valid ZIP archive.")
            return

        entries = list_zip_images(zf)
        if len(entries) > MAX_ZIP_IMAGES:
            m.SKIPPED_FILES.labels("over_limit").inc(len(entries) - MAX_ZIP_IMAGES)
            st.warning(f"ZIP has {len(entries)} images; only the first {MAX_ZIP_IMAGES} will be processed.")
            entries = entries[:MAX_ZIP_IMAGES]
        if not entries:
            m.ERRORS.labels("bulk", "empty_zip").inc()
            st.warning("No supported images found in the ZIP.")
            return

        n = len(entries)
        m.ZIP_IMAGE_COUNT.observe(n)
        m.BULK_QUEUE.inc(n)
        progress = st.progress(0.0, text=f"0 / {n}")
        rows, done = [], 0
        t0 = time.perf_counter()
        try:
            for info in entries:
                rows.append(process_one(info.filename, zf.read(info), "bulk", ip, sid, res))
                done += 1
                m.BULK_QUEUE.dec()
                progress.progress(done / n, text=f"{done} / {n}: {info.filename}")
        finally:
            # If the user interrupts (new upload or closes tab), don't leave the gauge stuck
            # ignore remaining images in the queue
            m.BULK_QUEUE.dec(n - done)
        total_s = time.perf_counter() - t0
        m.BULK_JOB_SECONDS.observe(total_s)

    df = pd.DataFrame(rows)
    ok = int((df["status"] == "ok").sum())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Images captioned", ok)
    c2.metric("Failed", n - ok)
    c3.metric("Total time (s)", f"{total_s:.1f}")
    c4.metric("Avg per image (s)", f"{total_s / n:.2f}")
    st.dataframe(df, hide_index=True)
    st.download_button("Download captions (CSV)", df.to_csv(index=False), file_name="captions.csv", mime="text/csv", on_click="ignore")
    
# Page
start_metrics()
res = load_model()
ip, sid = client_info()
m.record_session(sid)
st.title("CPU Image Captioning")
st.caption(f"Model: `{MODEL_NAME}` on CPU · metrics at http://localhost:{METRICS_PORT}/metrics")

with st.sidebar:
    st.header("Client info")
    st.write(f"**IP:** {ip}")
    st.write(f"**Session:** {sid}")
    st.write(f"**Torch threads:** {torch.get_num_threads()}")

tab_single, tab_bulk = st.tabs(["Single image", "Bulk (ZIP)"])

with tab_single:
    up = st.file_uploader("Upload an image", type=[e.lstrip(".") for e in IMAGE_EXTS], key="single")
    if up is not None and st.button("Generate caption", type="primary"):
        run_single(up, ip, sid, res)

with tab_bulk:
    upz = st.file_uploader("Upload a ZIP of images", type=["zip"], key="bulk")
    if upz is not None and st.button("Caption all images", type="primary"):
        run_bulk(upz, ip, sid, res)