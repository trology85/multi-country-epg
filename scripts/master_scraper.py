import requests
import gzip
import xml.etree.ElementTree as ET
import re
from bs4 import BeautifulSoup
import io
import os
from datetime import datetime, timedelta
import urllib3
import html as html_lib
from urllib.parse import urljoin, urlparse, parse_qs
from playwright.sync_api import sync_playwright

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- AYARLAR ---
SOURCES = {
    "DE": "https://epgshare01.online/epgshare01/epg_ripper_DE1.xml.gz",
    "FR": "https://epgshare01.online/epgshare01/epg_ripper_FR1.xml.gz",
    "GR": "https://epgshare01.online/epgshare01/epg_ripper_GR1.xml.gz"
}

WANTED_CHANNELS = {
    "RTL.de": "RTL", "ProSieben.de": "Pro7", "SAT.1.de": "SAT 1", "VOX.de": "Vox", "ZDF.de": "ZDF",
    "TF1.fr": "TF1", "M6.fr": "M6", "France2.fr": "France.2", "CanalPlus.fr": "Canal.Plus", "RTL.9.fr": "RTL 9",
    "ERT1.gr": "ERT1", "Mega.gr": "Mega", "Ant1.gr": "ANT1.gr", "Skai.gr": "Skai"
}

# --- YENI KAYNAK KANALLARI (Tivibu & Tivi6) ---
TIVIBU_CHANNELS = {
    "TİVİBU.SPOR.1.tr": "TİVİBU SPOR 1",
    "TİVİBU.SPOR.2.tr": "TİVİBU SPOR 2",
    "TİVİBU.SPOR.3.tr": "TİVİBU SPOR 3",
    "TİVİBU.SPOR.4.tr": "TİVİBU SPOR 4",
    "TİVİ6.tr": "Tivi6",
    "TİVİ.6.tr": "TİVİ6"
}

DESC_TARGET_CHANNELS = {
    "trt 1",
    "star",
    "atv",
    "show tv",
    "kanal d",
    "now tv",
    "beyaz tv",
    "tv 8",
    "360 tv",
    "tv 2",
}

CHANNEL_ALIASES = {
    "trt1": "trt 1",
    "trt 1": "trt 1",

    "star": "star",
    "atv": "atv",

    "show": "show tv",
    "show tv": "show tv",

    "kanal d": "kanal d",

    "now": "now tv",
    "now tv": "now tv",
    "fox": "now tv",

    "beyaz": "beyaz tv",
    "beyaz tv": "beyaz tv",

    "tv8": "tv 8",
    "tv 8": "tv 8",

    "360": "360 tv",
    "360 tv": "360 tv",

    "tv2": "tv 2",
    "tv 2": "tv 2",
}

description_cache = {}

BASE_URL = "https://www.turksatkablo.com.tr/"

DAY_CODE_MAP = {
    0: "b",
    1: "y",
    2: "s",
}

def normalize_program_title(title: str) -> str:
    text = html_lib.unescape(title or "")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text

def get_turksat_detail_links_for_day(day_index: int):
    if day_index not in DAY_CODE_MAP:
        return {}

    page_url = f"{BASE_URL}yayin-akisi.aspx?i={DAY_CODE_MAP[day_index]}"
    raw_pairs = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(page_url, wait_until="domcontentloaded", timeout=30000)

        try:
            page.wait_for_selector("a.ymodal", timeout=20000)
        except Exception:
            print(f"⚠️ {page_url} sayfasında ymodal bekleme süresi doldu.")
            browser.close()
            return {}

        links = page.locator("a.ymodal")
        count = links.count()

        for idx in range(count):
            el = links.nth(idx)
            title = normalize_program_title(el.inner_text())
            href = el.get_attribute("data-href") or ""
            if not href or not title:
                continue

            full_url = urljoin(BASE_URL, href)
            qs = parse_qs(urlparse(full_url).query)
            kid = (qs.get("kID") or [""])[0]
            eid = (qs.get("eID") or [""])[0]

            if kid and eid:
                raw_pairs.append((kid, title, full_url))

        browser.close()

    result = {}
    for kid, title, full_url in raw_pairs:
        key = (str(kid), title)
        result.setdefault(key, []).append(full_url)

    return result

def get_program_detail_from_url(detail_url: str, channel_name: str):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.turksatkablo.com.tr/yayin-akisi.aspx",
    }

    try:
        resp = requests.get(detail_url, headers=headers, verify=False, timeout=10)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        detail = soup.select_one("div.program-detail")
        if not detail:
            return None

        p = detail.find("p")
        if not p:
            return None

        clean_desc = p.get_text(" ", strip=True)
        clean_desc = html_lib.unescape(clean_desc)
        clean_desc = re.sub(r"\s+", " ", clean_desc).strip()

        if len(clean_desc) > 5:
            print(f"      ↳ 📝 {channel_name} için detay başarıyla alındı.")
            return clean_desc

    except Exception as e:
        print(f"      ⚠️ Detay bağlantı hatası ({channel_name}): {e}")

    return None

def normalize_channel_name(name: str) -> str:
    text = html_lib.unescape(name or "").lower()
    text = text.replace(".", " ").replace("-", " ")
    text = re.sub(r"\bhd\b", "", text)
    text = re.sub(r"\bsd\b", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return CHANNEL_ALIASES.get(text, text)

def should_fetch_desc(channel_name: str) -> bool:
    return normalize_channel_name(channel_name) in DESC_TARGET_CHANNELS


def fetch_turksat_weekly(master_root):
    tr_now = datetime.utcnow() + timedelta(hours=3)
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.turksatkablo.com.tr/yayin-akisi.aspx",
    }

    print("🇹🇷 Türksat Haftalık Tarama Başlatıldı...")

    for i in range(7):
        target_date = tr_now + timedelta(days=i)
        day_str = target_date.strftime("%d").lstrip("0")
        url = f"https://www.turksatkablo.com.tr/userUpload/EPG/{day_str}.json"

        # Açıklama linkleri: şu an ilk 3 gün için
        rendered_detail_links = get_turksat_detail_links_for_day(i) if i in DAY_CODE_MAP else {}

        try:
            r = requests.get(url, headers=headers, verify=False, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if "k" in data:
                    print(f"✅ {target_date.strftime('%d.%m.%Y')} eklendi.")
                    for channel in data.get("k", []):
                        chan_name = channel.get("n", "Unknown").strip()
                        chan_id = chan_name.replace(" ", ".")
                        chan_kID = channel.get("i")
                        fetch_desc_for_this_channel = should_fetch_desc(chan_name)

                        if i == 0:
                            print(
                                f"KANAL DEBUG: {chan_name!r} -> "
                                f"{normalize_channel_name(chan_name)!r} -> "
                                f"desc={fetch_desc_for_this_channel}"
                            )

                        if i == 0:
                            c_elem = ET.SubElement(master_root, "channel", id=chan_id)
                            ET.SubElement(c_elem, "display-name").text = chan_name

                        date_prefix = target_date.strftime("%Y%m%d")

                        for prog in channel.get("p", []):
                            start_time = prog.get("c", "").replace(":", "")
                            stop_time = prog.get("d", "").replace(":", "")

                            current_stop_prefix = date_prefix
                            if int(stop_time) < int(start_time):
                                next_day = target_date + timedelta(days=1)
                                current_stop_prefix = next_day.strftime("%Y%m%d")

                            start = date_prefix + start_time + "00+0300"
                            stop = current_stop_prefix + stop_time + "00+0300"

                            p_elem = ET.SubElement(
                                master_root,
                                "programme",
                                start=start,
                                stop=stop,
                                channel=chan_id
                            )

                            title = prog.get("b", "Yayın Akışı")
                            ET.SubElement(p_elem, "title", lang="tr").text = title

                            if fetch_desc_for_this_channel and chan_kID:
                                norm_title = normalize_program_title(title)
                                key = (str(chan_kID), norm_title)

                                if i == 0:
                                    print(
                                        f"DETAY DEBUG: kanal={chan_name!r} "
                                        f"kID={chan_kID!r} "
                                        f"title={title!r} "
                                        f"key={key!r} "
                                        f"found={key in rendered_detail_links}"
                                    )

                                if key in rendered_detail_links and rendered_detail_links[key]:
                                    detail_url = rendered_detail_links[key].pop(0)
                                    description = get_program_detail_from_url(detail_url, chan_name)
                                    if description:
                                        ET.SubElement(p_elem, "desc", lang="tr").text = description

        except Exception as e:
            print(f"⚠️ Türksat hatası ({target_date.strftime('%d.%m')}): {e}")

def fetch_tivibu_spor(master_root):
    url = "https://epgshare01.online/epgshare01/epg_ripper_TR3.xml.gz"
    print("📡 Tivibu Spor ve TİVİ6 Verileri Çekiliyor...")
    try:
        resp = requests.get(url, timeout=60)
        with gzip.GzipFile(fileobj=io.BytesIO(resp.content)) as f:
            context = ET.iterparse(f, events=("end",))
            for _, elem in context:
                if elem.tag == "channel":
                    orig_id = elem.get("id")
                    if orig_id in TIVIBU_CHANNELS:
                        elem.set("id", TIVIBU_CHANNELS[orig_id])
                        # display-name kısmını da düzeltelim
                        dn = elem.find("display-name")
                        if dn is not None: dn.text = TIVIBU_CHANNELS[orig_id]
                        master_root.append(elem)
                
                if elem.tag == "programme":
                    orig_id = elem.get("channel")
                    if orig_id in TIVIBU_CHANNELS:
                        elem.set("channel", TIVIBU_CHANNELS[orig_id])
                        master_root.append(elem)
        print("✅ Tivibu ve TİVİ6 başarıyla eklendi.")
    except Exception as e:
        print(f"⚠️ Tivibu/TİVİ6 hatası: {e}")
        
def fetch_azerbaijan_weekly_channel(master_root, *, url, chan_id, display_names, log_name):
    headers = {"User-Agent": "Mozilla/5.0"}

    print(f"🇦 {log_name} verisi çekiliyor...")

    try:
        resp = requests.get(url, headers=headers, verify=False, timeout=20)
        if resp.status_code != 200:
            print(f"⚠️ {log_name} HTTP hatası: {resp.status_code}")
            return

        soup = BeautifulSoup(resp.text, "html.parser")
        day_cards = soup.find_all("div", class_="day-card")

        if not day_cards:
            print(f"⚠️ {log_name} day-card bulunamadı.")
            return

        parsed_items = []

        for card in day_cards:
            title_el = card.find("h3", class_="day-title")
            notes_el = card.find("div", class_="day-notes")

            if not title_el or not notes_el:
                continue

            title_text = title_el.get_text(" ", strip=True)
            date_match = re.search(r"(\d{2}\.\d{2}\.\d{4})", title_text)
            if not date_match:
                continue

            base_date = datetime.strptime(date_match.group(1), "%d.%m.%Y")

            p_el = notes_el.find("p")
            if not p_el:
                continue

            for br in p_el.find_all("br"):
                br.replace_with("\n")

            raw_text = p_el.get_text("\n", strip=True)
            lines = [line.strip() for line in raw_text.split("\n") if line.strip()]

            current_day = base_date
            prev_minutes = None

            for line in lines:
                line = re.sub(r"\s+", " ", line).strip()
                m = re.match(r"^(\d{1,2})[:.](\d{2})\s+(.+)$", line)
                if not m:
                    continue

                hh = int(m.group(1))
                mm = int(m.group(2))
                title = m.group(3).strip()
                if not title:
                    continue

                total_minutes = hh * 60 + mm

                if prev_minutes is not None and total_minutes < prev_minutes:
                    current_day += timedelta(days=1)

                source_dt = current_day.replace(hour=hh, minute=mm, second=0, microsecond=0)
                turkey_dt = source_dt - timedelta(hours=1)

                parsed_items.append((turkey_dt, title))
                prev_minutes = total_minutes

        if not parsed_items:
            print(f"⚠️ {log_name} için programme üretilemedi.")
            return

        parsed_items.sort(key=lambda x: x[0])

        c_elem = ET.SubElement(master_root, "channel", id=chan_id)
        for name in display_names:
            ET.SubElement(c_elem, "display-name").text = name

        for i, (start_dt, title) in enumerate(parsed_items):
            if i + 1 < len(parsed_items):
                stop_dt = parsed_items[i + 1][0]
            else:
                stop_dt = start_dt + timedelta(hours=1)

            start = start_dt.strftime("%Y%m%d%H%M%S") + "+0300"
            stop = stop_dt.strftime("%Y%m%d%H%M%S") + "+0300"

            p_elem = ET.SubElement(
                master_root,
                "programme",
                start=start,
                stop=stop,
                channel=chan_id
            )
            ET.SubElement(p_elem, "title", lang="tr").text = title

        print(f"✅ {log_name} başarıyla eklendi. ({len(parsed_items)} programme)")

    except Exception as e:
        print(f"⚠️ {log_name} hatası: {e}")


def fetch_idman_tv(master_root):
    fetch_azerbaijan_weekly_channel(
        master_root,
        url="https://idmantv.az/az/program",
        chan_id="Idman.TV",
        display_names=["İdman TV", "Idman TV"],
        log_name="İdman TV",
    )


def fetch_az_tv(master_root):
    fetch_azerbaijan_weekly_channel(
        master_root,
        url="https://aztv.az/az/program",
        chan_id="AZ.TV",
        display_names=["AZ TV", "AzTV", "Azərbaycan Televiziyası"],
        log_name="AZ TV",
    )


def fetch_medeniyyet_tv(master_root):
    fetch_azerbaijan_weekly_channel(
        master_root,
        url="https://medeniyyettv.az/az/program",
        chan_id="Medeniyyet.TV",
        display_names=["Mədəniyyət TV", "Medeniyyet TV"],
        log_name="Mədəniyyət TV",
    )

def create_master():
    master_root = ET.Element("tv", {"generator-info-name": "Weekly Master Scraper"})

    # 1. Türksat
    fetch_turksat_weekly(master_root)
    fetch_idman_tv(master_root)
    fetch_az_tv(master_root)
    fetch_medeniyyet_tv(master_root)

    # 2. Yabancılar
    for country, url in SOURCES.items():
        print(f"🌍 {country} verisi işleniyor...")
        try:
            resp = requests.get(url, timeout=60)
            with gzip.GzipFile(fileobj=io.BytesIO(resp.content)) as f:
                context = ET.iterparse(f, events=("end",))
                for _, elem in context:
                    if elem.tag == "channel":
                        orig_id = elem.get("id")
                        if orig_id in WANTED_CHANNELS:
                            elem.set("id", WANTED_CHANNELS[orig_id])
                            master_root.append(elem)
                    
                    if elem.tag == "programme":
                        orig_id = elem.get("channel")
                        if orig_id in WANTED_CHANNELS:
                            elem.set("channel", WANTED_CHANNELS[orig_id])
                            master_root.append(elem)
        except Exception as e:
            print(f"⚠️ {country} hatası: {e}")

    # 3. Tivibu Spor & TİVİ6
    fetch_tivibu_spor(master_root)

    # 保存 (Save)
    os.makedirs("epg", exist_ok=True)
    tree = ET.ElementTree(master_root)
    xml_path = "epg/master_epg.xml"
    
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)
    with open(xml_path, 'rb') as f_in, gzip.open(xml_path + ".gz", 'wb') as f_out:
        f_out.writelines(f_in)
    
    print("🚀 Tüm kaynaklar birleştirildi. Haftalık Master EPG Hazır!")

if __name__ == "__main__":
    create_master()
