import os
import json
import subprocess
import re
import cloudinary
import cloudinary.uploader
import requests

# ─── الإعدادات ───────────────────────────────────────────
PAGE_URL = "ضع رابط الصفحة هنا"
LAST_IDS_FILE = "processed_ids.json"
COOKIES_FILE = "/tmp/cookies.txt"
WEBHOOK_URL = os.environ["WEBHOOK_URL"]
GREEN_SCREEN_ID = os.environ["GREEN_SCREEN_ID"]
OUTRO_ID = os.environ["OUTRO_ID"]

cloudinary.config(
    cloud_name = os.environ["CLOUDINARY_CLOUD_NAME"],
    api_key    = os.environ["CLOUDINARY_API_KEY"],
    api_secret = os.environ["CLOUDINARY_API_SECRET"],
)

# ─── قائمة الفيديوهات المعالجة ───────────────────────────
def load_processed_ids():
    try:
        with open(LAST_IDS_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_processed_ids(ids):
    with open(LAST_IDS_FILE, "w") as f:
        json.dump(ids[-50:], f)

# ─── جلب روابط الفيديوهات عبر Selenium ──────────────────
def get_latest_videos():
    print("🔍 جلب الفيديوهات عبر Selenium...")

    script = """
import json
import time
import re
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

options = Options()
options.add_argument("--headless")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--disable-gpu")
options.add_argument("--window-size=1920,1080")
options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=options)

driver.get("https://www.facebook.com")
time.sleep(2)

cookies = []
try:
    with open("/tmp/cookies.txt", "r") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.strip().split("\\t")
            if len(parts) >= 7:
                cookies.append({
                    "name": parts[5],
                    "value": parts[6],
                    "domain": parts[0]
                })
except:
    pass

for cookie in cookies:
    try:
        driver.add_cookie(cookie)
    except:
        pass

driver.get("https://www.facebook.com/profile.php?id=PAGE_ID&sk=reels_tab")
time.sleep(8)

page_source = driver.page_source
driver.quit()

reel_links = re.findall(r'href="(/reel/([0-9]+)/[^"]*)"', page_source)

videos = []
seen = set()
for path, vid_id in reel_links:
    if vid_id not in seen:
        seen.add(vid_id)
        clean_url = "https://www.facebook.com/reel/" + vid_id + "/"
        videos.append({
            "id": vid_id,
            "title": "",
            "url": clean_url
        })

print(json.dumps(videos[:5]))
"""

    # استبدال PAGE_ID بالرابط الفعلي
    page_id = re.search(r'id=(\d+)', PAGE_URL)
    if page_id:
        script = script.replace("PAGE_ID", page_id.group(1))

    with open("/tmp/selenium_script.py", "w") as f:
        f.write(script)

    result = subprocess.run(
        ["python", "/tmp/selenium_script.py"],
        capture_output=True, text=True, timeout=90
    )

    print(f"stdout: {result.stdout[:500]}")
    if result.stderr:
        print(f"stderr: {result.stderr[:300]}")

    videos = []
    try:
        lines = result.stdout.strip().split("\n")
        for line in reversed(lines):
            if line.startswith("["):
                videos = json.loads(line)
                break
    except Exception as e:
        print(f"❌ خطأ: {e}")
        return []

    # جلب العنوان الحقيقي
    for v in videos:
        try:
            title_result = subprocess.run([
                "yt-dlp", "--get-title", "--no-warnings",
                "--cookies", COOKIES_FILE,
                v["url"]
            ], capture_output=True, text=True, timeout=30)
            title = title_result.stdout.strip()
            if title:
                v["title"] = title
                print(f"  📹 {v['id']} | {title[:50]}")
        except:
            v["title"] = "بدون عنوان"

    print(f"✅ تم جلب {len(videos)} فيديو")
    return videos

# ─── تحميل الفيديو ────────────────────────────────────────
def download_video(video):
    print(f"⬇️ تحميل: {video['url']}")
    result = subprocess.run([
        "yt-dlp",
        "--cookies", COOKIES_FILE,
        "-o", "/tmp/main_video.mp4",
        "--format", "best[ext=mp4]/best",
        "--no-warnings",
        video["url"]
    ], capture_output=True, text=True, timeout=300)

    if not os.path.exists("/tmp/main_video.mp4"):
        print(f"❌ فشل التحميل: {result.stderr[:200]}")
        return False
    print("✅ تم تحميل الفيديو")
    return True

# ─── جلب أبعاد الفيديو ───────────────────────────────────
def get_dimensions(video_path):
    probe = subprocess.run([
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", video_path
    ], capture_output=True, text=True)
    try:
        info = json.loads(probe.stdout)
        vstream = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
        w = vstream["width"] if vstream else 1080
        h = vstream["height"] if vstream else 1920
        return w, h
    except:
        return 1080, 1920

# ─── تحميل ملفات Cloudinary ──────────────────────────────
def download_from_cloudinary(public_id, output_path, resource_type="video"):
    url = f"https://res.cloudinary.com/{os.environ['CLOUDINARY_CLOUD_NAME']}/{resource_type}/upload/{public_id}"
    if resource_type == "video":
        url += ".mp4"
    else:
        url += ".png"
    subprocess.run(["wget", "-q", "-O", output_path, url], timeout=60)
    return os.path.exists(output_path)

# ─── إضافة Green Screen ──────────────────────────────────
def apply_green_screen(main_video, green_screen_video, output_path, w, h):
    print("🎨 إضافة Green Screen...")
    result = subprocess.run([
        "ffmpeg", "-y",
        "-i", main_video,
        "-i", green_screen_video,
        "-filter_complex",
        f"[1:v]scale={w}:{h},colorkey=0x00FF00:0.3:0.1[gs];[0:v][gs]overlay=0:0",
        "-codec:a", "copy",
        "-preset", "fast",
        output_path
    ], capture_output=True, text=True, timeout=600)

    if not os.path.exists(output_path):
        print(f"❌ فشل Green Screen: {result.stderr[:200]}")
        return False
    print("✅ تم إضافة Green Screen")
    return True

# ─── إضافة Outro ─────────────────────────────────────────
def add_outro(main_video, outro_video, output_path, w, h):
    print("🎬 إضافة Outro...")

    # تعديل أبعاد الـ Outro ليتناسب مع الفيديو الرئيسي
    result = subprocess.run([
        "ffmpeg", "-y",
        "-i", main_video,
        "-i", outro_video,
        "-filter_complex",
        f"[0:v]scale={w}:{h}[v0];[1:v]scale={w}:{h}[v1];[v0][0:a][v1][1:a]concat=n=2:v=1:a=1[outv][outa]",
        "-map", "[outv]",
        "-map", "[outa]",
        "-preset", "fast",
        output_path
    ], capture_output=True, text=True, timeout=600)

    if not os.path.exists(output_path):
        # محاولة بديلة إذا لم يكن للـ Outro صوت
        print("⚠️ محاولة بديلة للـ Outro...")
        result2 = subprocess.run([
            "ffmpeg", "-y",
            "-i", main_video,
            "-i", outro_video,
            "-filter_complex",
            f"[0:v]scale={w}:{h}[v0];[1:v]scale={w}:{h}[v1];[v0][v1]concat=n=2:v=1:a=0[outv]",
            "-map", "[outv]",
            "-an",
            "-preset", "fast",
            output_path
        ], capture_output=True, text=True, timeout=600)

        if not os.path.exists(output_path):
            print(f"❌ فشل Outro: {result2.stderr[:200]}")
            return False

    print("✅ تم إضافة Outro")
    return True

# ─── رفع الفيديو النهائي على Cloudinary ──────────────────
def upload_to_cloudinary(video_path):
    print("☁️ رفع على Cloudinary...")
    result = cloudinary.uploader.upload(
        video_path,
        resource_type="video",
        public_id="final_video",
        overwrite=True,
    )
    return result["secure_url"]

# ─── إرسال للـ Webhook ────────────────────────────────────
def send_to_webhook(video_url, title):
    print("📤 إرسال للـ Webhook...")
    response = requests.post(WEBHOOK_URL, json={
        "video_url": video_url,
        "title": title
    }, timeout=30)
    print(f"✅ تم الإرسال: {response.status_code}")

# ─── تنظيف الملفات المؤقتة ───────────────────────────────
def cleanup():
    files = [
        "/tmp/main_video.mp4",
        "/tmp/green_screen.mp4",
        "/tmp/outro.mp4",
        "/tmp/after_gs.mp4",
        "/tmp/final_video.mp4",
    ]
    for f in files:
        if os.path.exists(f):
            os.remove(f)

# ─── التنفيذ الرئيسي ──────────────────────────────────────
print("🤖 بدء تشغيل البوت...")
processed_ids = load_processed_ids()
print(f"📋 فيديوهات معالجة سابقاً: {len(processed_ids)}")

videos = get_latest_videos()

if not videos:
    print("❌ لم يتم جلب أي فيديو")
else:
    new_video = None
    for v in videos:
        if v["id"] not in processed_ids:
            new_video = v
            break

    if not new_video:
        print("ℹ️ لا يوجد فيديو جديد")
    else:
        print(f"🆕 فيديو جديد: {new_video['title'][:60]}")

        # 1 - تحميل الفيديو الرئيسي
        if not download_video(new_video):
            exit(1)

        # جلب الأبعاد
        w, h = get_dimensions("/tmp/main_video.mp4")
        print(f"📐 أبعاد الفيديو: {w}x{h}")

        # 2 - تحميل Green Screen من Cloudinary
        has_gs = download_from_cloudinary(GREEN_SCREEN_ID, "/tmp/green_screen.mp4", "video")

        # 3 - تطبيق Green Screen
        if has_gs:
            success = apply_green_screen(
                "/tmp/main_video.mp4",
                "/tmp/green_screen.mp4",
                "/tmp/after_gs.mp4",
                w, h
            )
            current_video = "/tmp/after_gs.mp4" if success else "/tmp/main_video.mp4"
        else:
            print("⚠️ لا يوجد Green Screen، سيتم التخطي")
            current_video = "/tmp/main_video.mp4"

        # 4 - تحميل Outro من Cloudinary
        has_outro = download_from_cloudinary(OUTRO_ID, "/tmp/outro.mp4", "video")

        # 5 - إضافة Outro
        if has_outro:
            success = add_outro(
                current_video,
                "/tmp/outro.mp4",
                "/tmp/final_video.mp4",
                w, h
            )
            final_video = "/tmp/final_video.mp4" if success else current_video
        else:
            print("⚠️ لا يوجد Outro، سيتم التخطي")
            final_video = current_video

        # 6 - رفع الفيديو النهائي
        final_url = upload_to_cloudinary(final_video)

        # 7 - إرسال للـ Webhook
        send_to_webhook(final_url, new_video["title"])

        # 8 - حفظ المعرّف
        processed_ids.append(new_video["id"])
        save_processed_ids(processed_ids)

        # 9 - تنظيف
        cleanup()

        print("🎉 اكتمل بنجاح!")
