import requests
import gzip
import xml.etree.ElementTree as ET
import re
from bs4 import BeautifulSoup
import io
import os
from datetime import datetime, timedelta
import urllib3

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
    "ERT1.gr": "ERT1", "Mega.gr": "Mega", "Ant1.gr": "ANT1", "Skai.gr": "Skai"
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

def fetch_turksat_weekly(master_root):
    tr_now = datetime.utcnow() + timedelta(hours=3)
    headers = {'User-Agent': 'Mozilla/5.0'}
    print("🇹🇷 Türksat Haftalık Tarama Başlatıldı...")
    
    for i in range(7):
        target_date = tr_now + timedelta(days=i)
        day_str = target_date.strftime("%d").lstrip('0')
        url = f"https://www.turksatkablo.com.tr/userUpload/EPG/{day_str}.json"
        
        try:
            r = requests.get(url, headers=headers, verify=False, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if 'k' in data:
                    print(f"✅ {target_date.strftime('%d.%m.%Y')} eklendi.")
                    for channel in data.get('k', []):
                        chan_name = channel.get('n', 'Unknown')
                        chan_id = chan_name.replace(" ", ".")
                        
                        if i == 0:
                            c_elem = ET.SubElement(master_root, "channel", id=chan_id)
                            ET.SubElement(c_elem, "display-name").text = chan_name

                        date_prefix = target_date.strftime('%Y%m%d')
                        for prog in channel.get('p', []):
                            start_time = prog.get('c', '').replace(":", "")
                            stop_time = prog.get('d', '').replace(":", "")
                            
                            # Gece yarısı devretme kontrolü (Stop Start'tan küçükse gün ekle)
                            current_stop_prefix = date_prefix
                            if int(stop_time) < int(start_time):
                                next_day = target_date + timedelta(days=1)
                                current_stop_prefix = next_day.strftime('%Y%m%d')

                            start = date_prefix + start_time + "00 +0300"
                            stop = current_stop_prefix + stop_time + "00 +0300"
                            
                            p_elem = ET.SubElement(master_root, "programme", start=start, stop=stop, channel=chan_id)
                            ET.SubElement(p_elem, "title", lang="tr").text = prog.get('b', 'Yayın Akışı')
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
        
def fetch_idman_tv(master_root):
    url = "https://idmantv.az/az/program"
    headers = {"User-Agent": "Mozilla/5.0"}
    chan_id = "Idman.TV"

    print("🇦 İdman TV verisi çekiliyor...")

    try:
        resp = requests.get(url, headers=headers, verify=False, timeout=20)
        if resp.status_code != 200:
            print(f"⚠️ İdman TV HTTP hatası: {resp.status_code}")
            return

        soup = BeautifulSoup(resp.text, "html.parser")
        day_cards = soup.find_all("div", class_="day-card")

        if not day_cards:
            print("⚠️ İdman TV day-card bulunamadı.")
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
                m = re.match(r"^(\d{2}):(\d{2})\s+(.+)$", line)
                if not m:
                    continue

                hh = int(m.group(1))
                mm = int(m.group(2))
                title = m.group(3).strip()

                total_minutes = hh * 60 + mm

                # Saat geri sardıysa ertesi güne geç
                if prev_minutes is not None and total_minutes < prev_minutes:
                    current_day += timedelta(days=1)

                # Önce Azerbaycan saatiyle oluştur
                source_dt = current_day.replace(hour=hh, minute=mm, second=0, microsecond=0)

                # Türkiye saati için 1 saat geri al
                turkey_dt = source_dt - timedelta(hours=1)

                parsed_items.append((turkey_dt, title))
                prev_minutes = total_minutes

        if not parsed_items:
            print("⚠️ İdman TV için programme üretilemedi.")
            return

        parsed_items.sort(key=lambda x: x[0])

        c_elem = ET.SubElement(master_root, "channel", id=chan_id)
        ET.SubElement(c_elem, "display-name").text = "İdman TV"
        ET.SubElement(c_elem, "display-name").text = "Idman TV"

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

        print(f"✅ İdman TV başarıyla eklendi. ({len(parsed_items)} programme)")

    except Exception as e:
        print(f"⚠️ İdman TV hatası: {e}")

def create_master():
    master_root = ET.Element("tv", {"generator-info-name": "Weekly Master Scraper"})

    # 1. Türksat
    fetch_turksat_weekly(master_root)
    fetch_idman_tv(master_root)

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
