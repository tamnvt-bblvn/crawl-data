import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import re
import shutil
import zipfile
from typing import Any, Callable, Optional

import requests

# Mặc định CLI (có thể ghi đè bằng biến môi trường hoặc tham số)
DEFAULT_URL = "https://backend.wallpics.app/api/category-list-with-wallpapers?type=3&wallpapers_per_category=27"
DEFAULT_HEADERS = {
    "Host": "backend.wallpics.app",
    "x-auth": "1774945363",
    "content-type": "application/json",
    "accept": "*/*",
    "x-token": "ab8e505ffd211ce71b93ed2a08ea94b7",
    "user-agent": "Wallpics/4 CFNetwork/3826.600.41 Darwin/24.6.0",
    "x-guest-id": "8df10fcb-cda3-4bf6-9dda-52ef291327e0",
}

BASE_DIR = "wallpapers_downloads"

# ThemeKit: CDN gốc cho thumb, package, preview
THEMEKIT_CDN_BASE = "https://cdn.woohoostudios.io/platform/themeKit"
THEME_SUBDIR = "theme"

# Wallpics sticker packs: API trả về data[] với sticker (path zip) + stickers[]
WALLPICS_BACKEND_BASE = "https://backend.wallpics.app"
STICKERS_SUBDIR = "stickers"
# Theme iOS (Lutech): API trả về list[] trực tiếp, ảnh theo id.
LUTECH_WALLPAPER_BASE = "https://theme-ios.lutech.one/themeios/wallpaper"
try:
    DOWNLOAD_WORKERS = max(1, min(32, int(os.getenv("DOWNLOAD_WORKERS", "8"))))
except ValueError:
    DOWNLOAD_WORKERS = 8


def _cdn_join(rel: str) -> str:
    rel = (rel or "").strip().lstrip("/")
    return f"{THEMEKIT_CDN_BASE}/{rel}" if rel else ""


def _wallpics_backend_url(path_or_url: str) -> str:
    """Path tương đối (vd /stickers/...) nối với backend Wallpics; URL đầy đủ giữ nguyên."""
    if not path_or_url:
        return ""
    p = (path_or_url or "").strip()
    if p.startswith("http://") or p.startswith("https://"):
        return p
    base = WALLPICS_BACKEND_BASE.rstrip("/")
    if not p.startswith("/"):
        p = "/" + p
    return base + p


def _image_ext_from_path(path: str) -> str:
    """Đuôi file ảnh từ đường dẫn (bỏ query); mặc định jpg."""
    base = os.path.basename((path or "").replace("\\", "/").split("?")[0])
    if "." in base:
        ext = base.rsplit(".", 1)[-1].lower()
        if ext.isalnum() and 1 <= len(ext) <= 8:
            return ext
    return "jpg"


def _sanitize_folder_name(name: str, max_len: int = 120) -> str:
    invalid = '<>:"/\\|?*\n\r\t'
    s = "".join("_" if c in invalid else c for c in name)
    s = s.strip(" .")
    return (s[:max_len] if s else "theme")


def _unique_theme_folder(base_name: str, key: str, used: set) -> str:
    base = _sanitize_folder_name(base_name) or (key[:16] if key else "theme")
    if base not in used:
        used.add(base)
        return base
    suffix = (key or "")[:12] or "dup"
    n = 0
    while True:
        cand = f"{base}_{suffix}" if n == 0 else f"{base}_{suffix}_{n}"
        if cand not in used:
            used.add(cand)
            return cand
        n += 1


def _letters_only_folder_name(text: Any, fallback: str = "subject", max_len: int = 80) -> str:
    """
    Giữ lại chữ và khoảng trắng từ chuỗi đầu vào để làm tên thư mục.
    Ví dụ: "💗Valentine 2026" -> "Valentine".
    """
    s = str(text or "")
    kept = "".join(ch for ch in s if ch.isalpha() or ch.isspace())
    kept = " ".join(kept.split())
    return _sanitize_folder_name(kept or fallback, max_len=max_len)


def parse_curl_command(curl_text: str) -> tuple[str, dict[str, str]]:
    """Trích URL và headers từ chuỗi lệnh curl."""
    text = curl_text.strip()
    if not text:
        raise ValueError("Chuỗi curl rỗng.")
    if not text.lower().startswith("curl"):
        text = "curl " + text

    headers: dict[str, str] = {}
    for m in re.finditer(r"-H\s+([\"'])(.+?)\1", text, re.DOTALL):
        part = m.group(2)
        if ":" not in part:
            continue
        name, _, value = part.partition(":")
        headers[name.strip()] = value.strip()

    quoted_urls = re.findall(r"[\"'](https?://[^\"'\s]+)[\"']", text)
    url = quoted_urls[-1] if quoted_urls else None
    if not url:
        bare = re.findall(r"https?://[^\s\"']+", text)
        url = bare[-1] if bare else None
    if not url:
        raise ValueError("Không tìm thấy URL trong lệnh curl.")

    return url, headers


def download_and_rename(url, folder_path, new_name):
    if not url:
        return False

    ext = url.split(".")[-1].split("?")[0]
    if not ext or len(ext) > 8:
        ext = "bin"
    file_name = f"{new_name}.{ext}"
    file_path = os.path.join(folder_path, file_name)

    if os.path.exists(file_path):
        return False

    try:
        response = requests.get(url, stream=True, timeout=30)
        if response.status_code == 200:
            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
    except Exception:
        return False
    return False


def download_url_to_file(url: str, dest_path: str) -> bool:
    """Tải URL về đúng đường dẫn file (tạo thư mục cha nếu cần)."""
    if not url:
        return False
    parent = os.path.dirname(dest_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.exists(dest_path):
        return False
    try:
        response = requests.get(url, stream=True, timeout=120)
        if response.status_code != 200:
            return False
        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception:
        return False


def _parallel_workers_for(total_tasks: int) -> int:
    return max(1, min(DOWNLOAD_WORKERS, max(1, total_tasks)))


def _collect_zip_paths(root: str) -> list[str]:
    out: list[str] = []
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if fn.lower().endswith(".zip"):
                out.append(os.path.join(dirpath, fn))
    return out


def _is_macosx_dir_name(name: str) -> bool:
    n = (name or "").strip().upper()
    return n in {"_MACOSX", "__MACOSX"}


def _remove_macosx_dirs_under(root: str) -> None:
    """Xoa cac thu muc metadata cua macOS sau khi giai nen."""
    for dirpath, dirnames, _ in os.walk(root, topdown=True):
        keep: list[str] = []
        for dn in dirnames:
            if _is_macosx_dir_name(dn):
                shutil.rmtree(os.path.join(dirpath, dn), ignore_errors=True)
            else:
                keep.append(dn)
        dirnames[:] = keep


def _move_dir_contents_to(src_dir: str, dst_parent: str) -> None:
    """Di chuyển toàn bộ nội dung trực tiếp của src_dir vào dst_parent (gộp thư mục trùng tên)."""
    for name in os.listdir(src_dir):
        if _is_macosx_dir_name(name):
            shutil.rmtree(os.path.join(src_dir, name), ignore_errors=True)
            continue
        src = os.path.join(src_dir, name)
        dst = os.path.join(dst_parent, name)
        if os.path.exists(dst):
            if os.path.isdir(dst) and os.path.isdir(src):
                _move_dir_contents_to(src, dst)
                shutil.rmtree(src, ignore_errors=True)
            elif os.path.isfile(dst) and os.path.isfile(src):
                try:
                    os.remove(dst)
                except OSError:
                    pass
                shutil.move(src, dst)
            else:
                shutil.move(src, dst)
        else:
            shutil.move(src, dst)


def _flatten_zip_wrapper_to_parent(dest: str, parent_dir: str) -> None:
    """
    Bỏ các cấp thư mục bọc vô nghĩa: khi zip chỉ là chuỗi thư mục lồng nhau (mỗi cấp chỉ có 1 thư mục con),
    đưa nội dung thật lên parent_dir (thư mục pack) và xóa cả cây dest.
    """
    if not os.path.isdir(dest):
        return
    _remove_macosx_dirs_under(dest)
    cur = dest
    while True:
        try:
            names = os.listdir(cur)
        except OSError:
            return
        dirs = [
            n
            for n in names
            if os.path.isdir(os.path.join(cur, n)) and not _is_macosx_dir_name(n)
        ]
        files = [n for n in names if os.path.isfile(os.path.join(cur, n))]
        if len(dirs) == 1 and len(files) == 0:
            cur = os.path.join(cur, dirs[0])
            continue
        break
    if cur == dest:
        try:
            names = os.listdir(dest)
        except OSError:
            return
        dirs = [n for n in names if os.path.isdir(os.path.join(dest, n))]
        files = [n for n in names if os.path.isfile(os.path.join(dest, n))]
        if len(dirs) == 0 and len(files) > 0:
            _move_dir_contents_to(dest, parent_dir)
            shutil.rmtree(dest, ignore_errors=True)
        return
    _move_dir_contents_to(cur, parent_dir)
    shutil.rmtree(dest, ignore_errors=True)


def _extract_zip_to_folder(zip_path: str) -> bool:
    """
    Giải nén zip vào thư mục tạm theo tên file, gộp bớt cấp bọc, rồi xóa file zip.
    """
    if not os.path.isfile(zip_path):
        return False
    parent_dir = os.path.dirname(zip_path)
    stem = os.path.splitext(os.path.basename(zip_path))[0]
    dest = os.path.join(parent_dir, stem)
    try:
        os.makedirs(dest, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(dest)
    except (zipfile.BadZipFile, OSError, ValueError):
        return False

    _remove_macosx_dirs_under(dest)
    _flatten_zip_wrapper_to_parent(dest, parent_dir)

    try:
        os.remove(zip_path)
    except OSError:
        pass
    return True


def unzip_all_under(
    root: str,
    progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
) -> tuple[int, int]:
    """
    Giải nén mọi file .zip dưới root; lặp lại cho tới khi không còn zip mới
    (zip lồng nhau sau khi giải nén lớp trước). Trả về (số thành công, số lỗi).
    """
    processed: set[str] = set()
    total_ok = 0
    total_fail = 0
    round_num = 0
    while round_num < 50:
        zips = [p for p in _collect_zip_paths(root) if p not in processed]
        if not zips:
            break
        nbatch = len(zips)
        for i, zp in enumerate(zips):
            processed.add(zp)
            rel = os.path.relpath(zp, root)
            if progress_callback:
                progress_callback(
                    {
                        "phase": "unzip",
                        "current": i + 1,
                        "total": nbatch,
                        "message": f"Giải nén ({i + 1}/{nbatch}, lượt {round_num + 1}): {rel}",
                    }
                )
            if _extract_zip_to_folder(zp):
                total_ok += 1
            else:
                total_fail += 1
        round_num += 1
    if progress_callback:
        progress_callback(
            {
                "phase": "unzip",
                "current": max(total_ok + total_fail, 1),
                "total": max(total_ok + total_fail, 1),
                "message": f"Giải nén xong: {total_ok} file OK, {total_fail} lỗi.",
            }
        )
    return total_ok, total_fail


def _detect_json_kind(data_json: Any) -> Optional[str]:
    """ThemeKit | Sticker packs | Wallpics wallpaper list | Lutech wallpaper list."""
    if isinstance(data_json, list):
        if data_json and isinstance(data_json[0], dict):
            first = data_json[0]
            # Theme iOS/Lutech: top-level list object, có id + subject.
            if "id" in first and "subject" in first:
                return "lutech_wallpapers"
        return None

    if not isinstance(data_json, dict):
        return None
    d = data_json.get("data")
    if isinstance(d, dict) and "categoryList" in d:
        return "themekit"
    if isinstance(d, list):
        if d and isinstance(d[0], dict):
            first = d[0]
            # Uu tien nhan dien wallpaper list cu de tranh nham sang sticker packs.
            if isinstance(first.get("wallpapers"), list):
                return "wallpics"
            if "sticker" in first and isinstance(first.get("stickers"), list):
                return "stickers"
        return "wallpics"
    return None


def _iter_download_tasks(data_json: dict, base_dir: str) -> list[tuple[str, str, str, str, int]]:
    """Wallpics tasks cho 2 dạng JSON: nested-category va flat-list."""
    tasks: list[tuple[str, str, str, str, int]] = []
    for item in data_json.get("data", []):
        if not isinstance(item, dict):
            continue

        # Case 1 (cu): category chua wallpapers[]
        nested = item.get("wallpapers")
        if isinstance(nested, list) and nested and isinstance(nested[0], dict) and isinstance(nested[0].get("wallpapers"), list):
            for wp_item in nested:
                wp_slug = str(wp_item.get("slug", "unknown-wallpaper"))
                wp_slug_folder = os.path.join(base_dir, wp_slug)
                files_list = wp_item.get("wallpapers", [])
                if not files_list:
                    continue
                for idx, file_data in enumerate(files_list, start=1):
                    pair_folder = os.path.join(wp_slug_folder, str(idx))
                    for key, new_name in (("image", "wallpaper"), ("thumbnail", "thumbnail")):
                        u = file_data.get(key)
                        if u:
                            tasks.append((u, pair_folder, new_name, wp_slug, idx))
            continue

        # Case 2 (moi): flat wallpaper item co wallpaper/upscaled/thumbnail...
        wp_slug = str(item.get("slug") or item.get("id") or "unknown-wallpaper")
        wp_folder = os.path.join(base_dir, wp_slug)
        for key, new_name in (
            ("wallpaper", "wallpaper"),
            ("upscaled", "upscaled"),
            ("thumbnail", "thumbnail"),
        ):
            u = item.get(key)
            if u:
                tasks.append((u, wp_folder, new_name, wp_slug, 1))
    return tasks


def _iter_lutech_wallpaper_tasks(data_json: list[dict[str, Any]], base_dir: str) -> list[tuple[str, str, str, str]]:
    """
    Mỗi item sinh 2 task:
    - thumbnail.webp: .../webp/wallpaper{id}.webp
    - wallpaper.png: .../png/wallpaper{id}.png

    Cấu trúc thư mục:
    base_dir/<subject-chi-lay-chu>/<id>/{thumbnail.webp, wallpaper.png}
    """
    tasks: list[tuple[str, str, str, str]] = []
    for item in data_json:
        if not isinstance(item, dict):
            continue
        wallpaper_id = str(item.get("id") or "").strip()
        if not wallpaper_id:
            continue
        subject_folder = _letters_only_folder_name(item.get("subject"), fallback="subject")
        pair_folder = os.path.join(base_dir, subject_folder, wallpaper_id)
        webp_url = f"{LUTECH_WALLPAPER_BASE}/webp/wallpaper{wallpaper_id}.webp"
        png_url = f"{LUTECH_WALLPAPER_BASE}/png/wallpaper{wallpaper_id}.png"
        tasks.append((webp_url, os.path.join(pair_folder, "thumbnail.webp"), f"{subject_folder}/{wallpaper_id}/thumbnail", subject_folder))
        tasks.append((png_url, os.path.join(pair_folder, "wallpaper.png"), f"{subject_folder}/{wallpaper_id}/wallpaper", subject_folder))
    return tasks


def _iter_themekit_tasks(theme_root: str, data_json: dict) -> list[tuple[str, str, str]]:
    """(url, dest_path, log_label)"""
    tasks: list[tuple[str, str, str]] = []
    data = data_json.get("data") or {}
    used_names: set = set()
    for cat in data.get("categoryList") or []:
        for res in cat.get("resourceList") or []:
            name = res.get("name") or res.get("key") or "theme"
            key = str(res.get("key") or "")
            folder = _unique_theme_folder(str(name), key, used_names)
            rf = os.path.join(theme_root, folder)

            thumb = res.get("thumb")
            if thumb:
                u = _cdn_join(thumb)
                ext = _image_ext_from_path(thumb)
                thumb_name = f"thumb.{ext}"
                dest = os.path.join(rf, thumb_name)
                tasks.append((u, dest, f"{folder}/{thumb_name}"))

            pkg = res.get("packageUrl")
            if pkg:
                u = _cdn_join(pkg)
                dest = os.path.join(rf, os.path.basename(pkg.replace("\\", "/")))
                tasks.append((u, dest, f"{folder}/zip:{os.path.basename(dest)}"))

            for rel in res.get("previewLongList") or []:
                u = _cdn_join(rel)
                dest = os.path.join(rf, "previewLongList", os.path.basename(rel.replace("\\", "/")))
                tasks.append((u, dest, f"{folder}/previewLongList/{os.path.basename(dest)}"))

            for rel in res.get("previewShortList") or []:
                u = _cdn_join(rel)
                dest = os.path.join(rf, "previewShortList", os.path.basename(rel.replace("\\", "/")))
                tasks.append((u, dest, f"{folder}/previewShortList/{os.path.basename(dest)}"))

    return tasks


def _iter_sticker_pack_tasks(stickers_root: str, data_json: dict) -> list[tuple[str, str, str]]:
    """Chỉ tải file zip từ trường sticker (nối backend). (url, dest, log_label)."""
    tasks: list[tuple[str, str, str]] = []
    used_names: set = set()
    for item in data_json.get("data") or []:
        if not isinstance(item, dict):
            continue
        pid = item.get("id", "unknown")
        orig = item.get("sticker_original_name") or "pack"
        base_folder = _sanitize_folder_name(os.path.splitext(str(orig))[0]) or str(pid)
        folder = _unique_theme_folder(base_folder, str(pid), used_names)
        rf = os.path.join(stickers_root, folder)

        st = item.get("sticker")
        if st:
            u = _wallpics_backend_url(st)
            zip_name = os.path.basename(str(st).replace("\\", "/").split("?")[0])
            if not zip_name:
                zip_name = f"pack_{pid}.zip"
            dest = os.path.join(rf, zip_name)
            tasks.append((u, dest, f"{folder}/{zip_name}"))

    return tasks


def _download_stickers_from_json(
    data_json: dict,
    stickers_root: str,
    progress_callback: Optional[Callable[[dict[str, Any]], None]],
    result: dict,
) -> dict:
    def _p(payload: dict[str, Any]) -> None:
        if progress_callback:
            progress_callback(payload)

    _p({"phase": "parse", "current": 0, "total": 0, "message": "Đang phân tích sticker packs (Wallpics)…"})
    tasks = _iter_sticker_pack_tasks(stickers_root, data_json)
    total = len(tasks)
    if total == 0:
        result["error"] = "Sticker packs: không có file nào (data rỗng hoặc thiếu sticker)."
        return result

    result["kind"] = "stickers"
    _p(
        {
            "phase": "download",
            "current": 0,
            "total": total,
            "message": f"Tìm thấy {total} file (thư mục {STICKERS_SUBDIR}/).",
        }
    )

    workers = _parallel_workers_for(total)
    futures: dict = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for url, dest_path, label in tasks:
            fut = executor.submit(download_url_to_file, url, dest_path)
            futures[fut] = label

        done = 0
        for fut in as_completed(futures):
            label = futures[fut]
            try:
                ok = bool(fut.result())
            except Exception:
                ok = False
            if ok:
                result["files_ok"] += 1
            else:
                result["files_fail"] += 1
            done += 1
            _p(
                {
                    "phase": "download",
                    "current": done,
                    "total": total,
                    "message": f"Đã tải {done}/{total}: {label}" + (" ✓" if ok else " (lỗi/bỏ qua)"),
                    "item": label,
                }
            )

    u_ok, u_fail = unzip_all_under(stickers_root, _p)
    result["unzip_ok"] = u_ok
    result["unzip_fail"] = u_fail

    if os.path.isdir(stickers_root):
        sub = [
            x
            for x in os.listdir(stickers_root)
            if os.path.isdir(os.path.join(stickers_root, x))
        ]
        result["slug_count"] = len(sub)
    else:
        result["slug_count"] = 0

    result["ok"] = True
    _p(
        {
            "phase": "download",
            "current": total,
            "total": total,
            "message": (
                f"Sticker packs xong: tải {result['files_ok']} OK, {result['files_fail']} lỗi; "
                f"giải nén {u_ok} OK, {u_fail} lỗi."
            ),
        }
    )
    return result


def _download_wallpics_from_json(
    data_json: dict,
    base_dir: str,
    progress_callback: Optional[Callable[[dict[str, Any]], None]],
    result: dict,
) -> dict:
    def _p(payload: dict[str, Any]) -> None:
        if progress_callback:
            progress_callback(payload)

    _p({"phase": "parse", "current": 0, "total": 0, "message": "Đang phân tích danh sách file (Wallpics)…"})
    tasks = _iter_download_tasks(data_json, base_dir)
    total = len(tasks)
    if total == 0:
        result["error"] = "Không có file nào cần tải (danh sách rỗng)."
        return result

    result["kind"] = "wallpics"
    _p(
        {
            "phase": "download",
            "current": 0,
            "total": total,
            "message": f"Tìm thấy {total} file (Wallpics).",
        }
    )

    workers = _parallel_workers_for(total)
    futures: dict = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for url, pair_folder, new_name, slug, pair_idx in tasks:
            os.makedirs(pair_folder, exist_ok=True)
            label = f"{slug}/{pair_idx}/{new_name}"
            fut = executor.submit(download_and_rename, url, pair_folder, new_name)
            futures[fut] = (label, slug)

        done = 0
        for fut in as_completed(futures):
            label, slug = futures[fut]
            try:
                ok = bool(fut.result())
            except Exception:
                ok = False
            if ok:
                result["files_ok"] += 1
            else:
                result["files_fail"] += 1
            done += 1
            _p(
                {
                    "phase": "download",
                    "current": done,
                    "total": total,
                    "message": f"Đã tải {done}/{total}: {label}" + (" ✓" if ok else " (lỗi/bỏ qua)"),
                    "slug": slug,
                    "item": label,
                }
            )

    all_items = os.listdir(base_dir)
    slug_folders = [item for item in all_items if os.path.isdir(os.path.join(base_dir, item))]
    result["ok"] = True
    result["slug_count"] = len(slug_folders)
    _p(
        {
            "phase": "download",
            "current": total,
            "total": total,
            "message": f"Đã tải xong: {result['files_ok']} thành công, {result['files_fail']} bỏ qua/lỗi.",
        }
    )
    return result


def _download_lutech_wallpapers_from_json(
    data_json: list[dict[str, Any]],
    base_dir: str,
    progress_callback: Optional[Callable[[dict[str, Any]], None]],
    result: dict,
) -> dict:
    def _p(payload: dict[str, Any]) -> None:
        if progress_callback:
            progress_callback(payload)

    _p({"phase": "parse", "current": 0, "total": 0, "message": "Đang phân tích wallpaper list (Lutech)…"})
    tasks = _iter_lutech_wallpaper_tasks(data_json, base_dir)
    total = len(tasks)
    if total == 0:
        result["error"] = "Lutech wallpaper: không có item hợp lệ (thiếu id/subject)."
        return result

    result["kind"] = "lutech_wallpapers"
    _p(
        {
            "phase": "download",
            "current": 0,
            "total": total,
            "message": f"Tìm thấy {total} file (Lutech wallpaper: thumbnail.webp + wallpaper.png theo id).",
        }
    )

    workers = _parallel_workers_for(total)
    futures: dict = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for url, dest_path, label, subject_folder in tasks:
            fut = executor.submit(download_url_to_file, url, dest_path)
            futures[fut] = (label, subject_folder)

        done = 0
        for fut in as_completed(futures):
            label, subject_folder = futures[fut]
            try:
                ok = bool(fut.result())
            except Exception:
                ok = False
            if ok:
                result["files_ok"] += 1
            else:
                result["files_fail"] += 1
            done += 1
            _p(
                {
                    "phase": "download",
                    "current": done,
                    "total": total,
                    "message": f"Đã tải {done}/{total}: {label}" + (" ✓" if ok else " (lỗi/bỏ qua)"),
                    "slug": subject_folder,
                    "item": label,
                }
            )

    if os.path.isdir(base_dir):
        subject_folders = [
            item for item in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, item))
        ]
        result["slug_count"] = len(subject_folders)
    else:
        result["slug_count"] = 0

    result["ok"] = True
    _p(
        {
            "phase": "download",
            "current": total,
            "total": total,
            "message": f"Lutech wallpaper xong: {result['files_ok']} file OK, {result['files_fail']} lỗi/bỏ qua.",
        }
    )
    return result


def _download_themekit_from_json(
    data_json: dict,
    theme_root: str,
    progress_callback: Optional[Callable[[dict[str, Any]], None]],
    result: dict,
) -> dict:
    def _p(payload: dict[str, Any]) -> None:
        if progress_callback:
            progress_callback(payload)

    _p({"phase": "parse", "current": 0, "total": 0, "message": "Đang phân tích danh sách file (ThemeKit)…"})
    tasks = _iter_themekit_tasks(theme_root, data_json)
    total = len(tasks)
    if total == 0:
        result["error"] = "ThemeKit: không có file nào trong categoryList/resourceList."
        return result

    result["kind"] = "themekit"
    _p(
        {
            "phase": "download",
            "current": 0,
            "total": total,
            "message": f"Tìm thấy {total} file (ThemeKit, thư mục {THEME_SUBDIR}/).",
        }
    )

    workers = _parallel_workers_for(total)
    futures: dict = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for url, dest_path, label in tasks:
            fut = executor.submit(download_url_to_file, url, dest_path)
            futures[fut] = label

        done = 0
        for fut in as_completed(futures):
            label = futures[fut]
            try:
                ok = bool(fut.result())
            except Exception:
                ok = False
            if ok:
                result["files_ok"] += 1
            else:
                result["files_fail"] += 1
            done += 1
            _p(
                {
                    "phase": "download",
                    "current": done,
                    "total": total,
                    "message": f"Đã tải {done}/{total}: {label}" + (" ✓" if ok else " (lỗi/bỏ qua)"),
                    "item": label,
                }
            )

    u_ok, u_fail = unzip_all_under(theme_root, _p)
    result["unzip_ok"] = u_ok
    result["unzip_fail"] = u_fail

    if os.path.isdir(theme_root):
        theme_folders = [
            item
            for item in os.listdir(theme_root)
            if os.path.isdir(os.path.join(theme_root, item))
        ]
        result["slug_count"] = len(theme_folders)
    else:
        result["slug_count"] = 0

    result["ok"] = True
    _p(
        {
            "phase": "download",
            "current": total,
            "total": total,
            "message": (
                f"ThemeKit xong: tải {result['files_ok']} OK, {result['files_fail']} lỗi; "
                f"giải nén {u_ok} OK, {u_fail} lỗi."
            ),
        }
    )
    return result


def download_resources(
    api_url: str,
    headers: dict,
    base_dir: str,
    progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
) -> dict:
    """
    Gọi API, nhận diện ThemeKit / sticker packs / wallpaper Wallpics / wallpaper Lutech, tải về base_dir.
    ThemeKit: base_dir/theme/… | Sticker: base_dir/stickers/… | Wallpaper: slug trực tiếp dưới base_dir.
    Trả về: ok, slug_count, files_ok, files_fail, error, kind.
    """
    if not os.path.exists(base_dir):
        os.makedirs(base_dir)

    result: dict = {
        "ok": False,
        "slug_count": 0,
        "files_ok": 0,
        "files_fail": 0,
        "unzip_ok": 0,
        "unzip_fail": 0,
        "error": None,
        "kind": None,
    }

    def _p(payload: dict[str, Any]) -> None:
        if progress_callback:
            progress_callback(payload)

    try:
        _p({"phase": "api", "current": 0, "total": 1, "message": "Đang gọi API…"})
        response = requests.get(api_url, headers=headers, timeout=120)
        response.raise_for_status()
        data_json = response.json()

        kind = _detect_json_kind(data_json)
        if kind == "themekit":
            if data_json.get("errorCode", 0) != 0:
                msg = data_json.get("errorMsg") or "API ThemeKit lỗi."
                result["error"] = f"ThemeKit errorCode={data_json.get('errorCode')}: {msg}"
                return result
            theme_root = os.path.join(base_dir, THEME_SUBDIR)
            os.makedirs(theme_root, exist_ok=True)
            return _download_themekit_from_json(data_json, theme_root, progress_callback, result)

        if kind == "stickers":
            if data_json.get("status") and data_json.get("status") != "success":
                result["error"] = f"API sticker packs: status={data_json.get('status')!r}"
                return result
            stickers_root = os.path.join(base_dir, STICKERS_SUBDIR)
            os.makedirs(stickers_root, exist_ok=True)
            return _download_stickers_from_json(data_json, stickers_root, progress_callback, result)

        if kind == "wallpics":
            return _download_wallpics_from_json(data_json, base_dir, progress_callback, result)

        if kind == "lutech_wallpapers":
            return _download_lutech_wallpapers_from_json(data_json, base_dir, progress_callback, result)

        result["error"] = (
            "Không nhận dạng JSON (Wallpics wallpaper list; ThemeKit data.categoryList; "
            "Sticker packs data[] có sticker + stickers; Lutech list[] có id + subject)."
        )
        return result
    except Exception as e:
        result["error"] = str(e)
        _p({"phase": "error", "message": str(e)})

    return result


def download_wallpapers(
    api_url: str,
    headers: dict,
    base_dir: str,
    progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
) -> dict:
    """Tương thích cũ: gọi download_resources (Wallpics + ThemeKit)."""
    return download_resources(api_url, headers, base_dir, progress_callback)


def main():
    parser = argparse.ArgumentParser(description="Tải wallpaper từ API Wallpics")
    parser.add_argument(
        "--curl",
        type=str,
        default=None,
        help="Lệnh curl đầy đủ (nếu có thì bỏ qua URL/headers mặc định)",
    )
    args = parser.parse_args()

    if args.curl:
        url, headers = parse_curl_command(args.curl)
    else:
        url, headers = DEFAULT_URL, DEFAULT_HEADERS.copy()

    print("--- Bắt đầu tải (ThemeKit / Sticker packs / Wallpics — tự nhận diện) ---")
    out = download_resources(url, headers, BASE_DIR)
    if out["ok"]:
        print("\n" + "=" * 40)
        print("HOÀN THÀNH!")
        k = out.get("kind")
        if k == "themekit":
            print(
                f"ThemeKit — thư mục '{BASE_DIR}/{THEME_SUBDIR}': "
                f"{out['slug_count']} theme, {out['files_ok']} file tải OK; "
                f"giải nén zip: {out.get('unzip_ok', 0)} OK, {out.get('unzip_fail', 0)} lỗi."
            )
        elif k == "stickers":
            print(
                f"Sticker packs — '{BASE_DIR}/{STICKERS_SUBDIR}': "
                f"{out['slug_count']} pack, {out['files_ok']} file tải OK; "
                f"giải nén zip: {out.get('unzip_ok', 0)} OK, {out.get('unzip_fail', 0)} lỗi."
            )
        else:
            print(f"Tổng số folder slug trong '{BASE_DIR}': {out['slug_count']}")
        print("=" * 40)
    else:
        print(f"Lỗi: {out.get('error')}")


if __name__ == "__main__":
    main()
