# hour_back.py
from flask import Flask, request, render_template, jsonify
from flask_cors import CORS
import os, json, time, re
from datetime import datetime, timedelta

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from bs4 import BeautifulSoup
import pandas as pd

app = Flask(__name__)
CORS(app)  # 다른 도메인 프론트에서 호출시 CORS 허용(필요하면 origins 제한)

# ====== 기준값 / 설정 ======
top30 = 168
avg_ref = 182.7
bottom70 = 194
START_DATE = os.environ.get("START_DATE", "2025-03-22")
MAX_DAYS   = int(os.environ.get("MAX_DAYS", "60"))  # 초기 풀스캔 부담 줄이기

# ====== 캐시 디렉토리/파일 (/data 볼륨) ======
CACHE_DIR = os.environ.get("CACHE_DIR", "/data")
os.makedirs(CACHE_DIR, exist_ok=True)
RUNTIME_CACHE_FILE  = os.path.join(CACHE_DIR, "runtime_cache.json")
SCHEDULE_CACHE_FILE = os.path.join(CACHE_DIR, "schedule_index.json")

def _load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def _save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def get_runtime_cache():
    return _load_json(RUNTIME_CACHE_FILE, {})

def set_runtime_cache(key, runtime_min):
    cache = get_runtime_cache()
    cache[key] = {"runtime_min": runtime_min}
    _save_json(RUNTIME_CACHE_FILE, cache)

def get_schedule_cache():
    return _load_json(SCHEDULE_CACHE_FILE, {})

def set_schedule_cache_for_date(date_str, games_minimal_list):
    cache = get_schedule_cache()
    cache[date_str] = games_minimal_list
    _save_json(SCHEDULE_CACHE_FILE, cache)

def make_runtime_key(game_id: str, game_date: str) -> str:
    return f"{game_id}_{game_date}"

def delete_all_caches():
    for p in [RUNTIME_CACHE_FILE, SCHEDULE_CACHE_FILE]:
        if os.path.exists(p):
            try: os.remove(p)
            except Exception: pass

# ====== Selenium 드라이버 (컨테이너 친화) ======
def make_driver():
    options = Options()
    options.binary_location = os.environ.get("CHROME_BIN", "/usr/bin/chromium")
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,1200")
    options.add_argument("--lang=ko-KR")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )

    # ✅ pageLoadStrategy는 이제 Options에 직접 설정
    options.page_load_strategy = "eager"

    driver_path = os.environ.get("CHROMEDRIVER", "/usr/bin/chromedriver")
    service = Service(executable_path=driver_path)

    # ✅ desired_capabilities 제거
    return webdriver.Chrome(service=service, options=options)


# ====== 크롤링 유틸 ======
def get_today_cards(driver):
    wait = WebDriverWait(driver, 15)
    today = datetime.today().strftime("%Y%m%d")
    url = f"https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx?gameDate={today}"
    driver.get(url)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div#contents")))
    time.sleep(0.6)  # 렌더링 여유
    soup = BeautifulSoup(driver.page_source, "html.parser")
    return soup.select("li.game-cont") or soup.select("li[class*='game-cont']")

def extract_match_info_from_card(card_li):
    home_nm = card_li.get("home_nm")
    away_nm = card_li.get("away_nm")
    g_id = card_li.get("g_id")
    g_dt = card_li.get("g_dt")

    if not (home_nm and away_nm):
        home_alt = card_li.select_one(".team.home .emb img")
        away_alt = card_li.select_one(".team.away .emb img")
        if away_alt and not away_nm: away_nm = away_alt.get("alt", "").strip() or None
        if home_alt and not home_nm: home_nm = home_alt.get("alt", "").strip() or None

    if not (home_nm and away_nm):
        txt = card_li.get_text(" ", strip=True)
        m = re.search(r"([A-Za-z가-힣]+)\s*vs\s*([A-Za-z가-힣]+)", txt, re.I)
        if m:
            a, b = m.group(1), m.group(2)
            away_nm = away_nm or a
            home_nm = home_nm or b

    if not (g_id and g_dt):
        a = card_li.select_one("a[href*='GameCenter/Main.aspx'][href*='gameId='][href*='gameDate=']")
        if a and a.has_attr("href"):
            href = a["href"]
            gm = re.search(r"gameId=([A-Z0-9]+)", href)
            dm = re.search(r"gameDate=(\d{8})", href)
            if gm: g_id = g_id or gm.group(1)
            if dm: g_dt = g_dt or dm.group(1)

    return {"home": home_nm, "away": away_nm, "g_id": g_id, "g_dt": g_dt}

def find_today_matches_for_team(driver, my_team):
    cards = get_today_cards(driver)
    results = []
    for li in cards:
        info = extract_match_info_from_card(li)
        home, away = info["home"], info["away"]
        if not (home and away): continue
        if my_team in {home, away}:
            rival = home if away == my_team else away
            info["rival"] = rival
            results.append(info)
    return results

def get_games_for_date(driver, date_str):
    cache = get_schedule_cache()
    if date_str in cache:
        return cache[date_str]

    wait = WebDriverWait(driver, 15)
    url = f"https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx?gameDate={date_str}"
    driver.get(url)
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div#contents")))
    except Exception:
        set_schedule_cache_for_date(date_str, [])
        return []

    time.sleep(0.5)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    cards = soup.select("li.game-cont") or soup.select("li[class*='game-cont']")
    games_minimal = []
    for li in cards:
        info = extract_match_info_from_card(li)
        if all([info.get("home"), info.get("away"), info.get("g_id"), info.get("g_dt")]):
            games_minimal.append({
                "home": info["home"],
                "away": info["away"],
                "g_id": info["g_id"],
                "g_dt": info["g_dt"],
            })

    set_schedule_cache_for_date(date_str, games_minimal)
    return games_minimal

def open_review_and_get_runtime(driver, game_id, game_date):
    today_str = datetime.today().strftime("%Y%m%d")
    use_cache = (game_date != today_str)
    key = make_runtime_key(game_id, game_date)

    if use_cache:
        rc = get_runtime_cache()
        hit = rc.get(key)
        if hit and isinstance(hit, dict) and "runtime_min" in hit:
            return hit["runtime_min"]

    wait = WebDriverWait(driver, 12)
    base = f"https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx?gameId={game_id}&gameDate={game_date}"
    driver.get(base)
    try:
        review_tab = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), '리뷰')]")))
        review_tab.click()
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.record-etc")))
    except Exception:
        driver.get(base + "&section=REVIEW")
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.record-etc")))
        except Exception:
            pass

    time.sleep(0.5)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    run_time_min = None
    record_etc = soup.select_one("div.record-etc")
    if record_etc:
        span = record_etc.select_one("span#txtRunTime")
        if span:
            runtime = span.get_text(strip=True)
            m = re.search(r"(\d{1,2})\s*[:：]\s*(\d{2})", runtime)
            if m:
                h, mnt = int(m.group(1)), int(m.group(2))
                run_time_min = h * 60 + mnt

    if use_cache and run_time_min is not None:
        set_runtime_cache(key, run_time_min)

    return run_time_min

def collect_history_avg_runtime(my_team, rival_set, start_date=START_DATE):
    d = make_driver()
    today_minus_1 = (datetime.today() - timedelta(days=1)).strftime("%Y%m%d")
    dr = pd.date_range(start=start_date, end=today_minus_1)
    if len(dr) > MAX_DAYS:
        dr = dr[-MAX_DAYS:]
    date_list = [d.strftime("%Y%m%d") for d in dr]

    run_times = []
    for date in date_list:
        games = get_games_for_date(d, date)
        if not games: continue
        for info in games:
            home, away, game_id, game_date = info["home"], info["away"], info["g_id"], info["g_dt"]
            if my_team in {home, away}:
                opponent = home if away == my_team else away
                if rival_set and opponent not in rival_set:
                    continue
                try:
                    rt = open_review_and_get_runtime(d, game_id, game_date)
                except Exception:
                    rt = None
                if rt is not None:
                    run_times.append(rt)

    try: d.quit()
    except: pass

    if run_times:
        avg_time = round(sum(run_times) / len(run_times), 1)
        return avg_time, run_times
    else:
        return None, []

# ====== 메인 라우트 (예외를 200으로 돌려 표시) ======
@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    avg_time = None
    css_class = ""
    msg = ""
    selected_team = None

    try:
        if request.method == "POST":
            MY_TEAM = request.form.get("myteam")
            selected_team = MY_TEAM
            if not MY_TEAM:
                result = "팀을 선택해주세요."
                return render_template("hour.html", result=result, avg_time=avg_time, css_class=css_class, msg=msg,
                                       selected_team=selected_team, top30=top30, avg_ref=avg_ref, bottom70=bottom70)

            d = make_driver()
            try:
                today_matches = find_today_matches_for_team(d, MY_TEAM)
            finally:
                try: d.quit()
                except: pass

            if not today_matches:
                result = f"{MY_TEAM}의 오늘 경기를 찾지 못했습니다."
                return render_template("hour.html", result=result, avg_time=avg_time, css_class=css_class, msg=msg,
                                       selected_team=selected_team, top30=top30, avg_ref=avg_ref, bottom70=bottom70)

            rivals_today = {m["rival"] for m in today_matches if m.get("rival")}
            rivals_str = ", ".join(rivals_today)
            result = f"오늘 {MY_TEAM}의 상대팀은 {rivals_str}입니다."

            try:
                avg_time, _ = collect_history_avg_runtime(MY_TEAM, rivals_today)
            except Exception:
                avg_time = None

            if avg_time is not None:
                if avg_time < top30:
                    css_class, msg = "fast", "빠르게 끝나는 경기입니다"
                elif avg_time < avg_ref:
                    css_class, msg = "normal", "일반적인 경기 소요 시간입니다"
                elif avg_time < bottom70:
                    css_class, msg = "bit-long", "조금 긴 편이에요"
                else:
                    css_class, msg = "long", "시간 오래 걸리는 매치업입니다"
                result = f"오늘 {MY_TEAM}의 상대팀은 {rivals_str}입니다.<br>과거 {MY_TEAM} vs {rivals_str} 평균 경기시간: {avg_time}분"
            else:
                result = f"오늘 {MY_TEAM}의 상대팀은 {rivals_str}입니다.<br>과거 경기 데이터가 없습니다."

        return render_template("hour.html", result=result, avg_time=avg_time, css_class=css_class, msg=msg,
                               selected_team=selected_team, top30=top30, avg_ref=avg_ref, bottom70=bottom70)
    except Exception as e:
        return f"오류가 발생했습니다: {type(e).__name__}: {str(e)}", 200

# ====== 진단/운영 엔드포인트 ======
def _file_info(path):
    if not os.path.exists(path):
        return {"exists": False}
    st = os.stat(path)
    return {
        "exists": True,
        "size_bytes": st.st_size,
        "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
        "path": os.path.abspath(path),
    }

@app.route("/healthz")
def healthz():
    return "ok", 200

@app.route("/selenium/env")
def selenium_env():
    import shutil
    return jsonify({
        "CACHE_DIR": os.path.abspath(CACHE_DIR),
        "CHROMEDRIVER": os.environ.get("CHROMEDRIVER"),
        "CHROME_BIN": os.environ.get("CHROME_BIN"),
        "which_chromedriver": shutil.which("chromedriver"),
        "which_chromium": shutil.which("chromium"),
        "driver_exists": os.path.exists(os.environ.get("CHROMEDRIVER", "/usr/bin/chromedriver")),
        "chrome_exists": os.path.exists(os.environ.get("CHROME_BIN", "/usr/bin/chromium")),
    })

@app.route("/selenium/smoke")
def selenium_smoke():
    try:
        d = make_driver()
        d.get("https://example.com")
        title = d.title
        d.quit()
        return jsonify({"ok": True, "title": title})
    except Exception as e:
        try: d.quit()
        except: pass
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {str(e)}"}), 500

@app.route("/cache/status")
def cache_status():
    return jsonify({
        "CACHE_DIR": os.path.abspath(CACHE_DIR),
        "runtime_cache": _file_info(RUNTIME_CACHE_FILE),
        "schedule_cache": _file_info(SCHEDULE_CACHE_FILE),
    })

@app.route("/cache/clear", methods=["POST"])
def cache_clear():
    deleted = []
    for p in [RUNTIME_CACHE_FILE, SCHEDULE_CACHE_FILE]:
        if os.path.exists(p):
            try:
                os.remove(p); deleted.append(os.path.basename(p))
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "deleted": deleted})

@app.route("/debug/today_matches")
def debug_today_matches():
    team = request.args.get("team")
    if not team:
        return jsonify({"ok": False, "error": "team query param required"}), 400
    d = make_driver()
    try:
        matches = find_today_matches_for_team(d, team)
    finally:
        try: d.quit()
        except: pass
    return jsonify({"ok": True, "team": team, "matches": matches})

@app.route("/debug/review_runtime")
def debug_review_runtime():
    gid = request.args.get("gameId")
    gdt = request.args.get("gameDate")
    if not gid or not gdt:
        return jsonify({"ok": False, "error": "gameId & gameDate required"}), 400
    d = make_driver()
    try:
        rt = open_review_and_get_runtime(d, gid, gdt)
    finally:
        try: d.quit()
        except: pass
    return jsonify({"ok": True, "gameId": gid, "gameDate": gdt, "runtime_min": rt})

if __name__ == "__main__":
    app.run(debug=True, port=5002, use_reloader=False)
