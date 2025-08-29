# hour_back.py
from flask import Flask, request, render_template, jsonify
from flask_cors import CORS
import os, json, time, re
from datetime import datetime, timedelta

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from bs4 import BeautifulSoup
import pandas as pd
import requests  # ★ 추가: 빠른 경로용

app = Flask(__name__)
CORS(app)

# ====== 기준값 / 설정 ======
top30 = 168
avg_ref = 182.7
bottom70 = 194
START_DATE = os.environ.get("START_DATE", "2025-03-22")

# ====== 캐시 디렉토리 ======
CACHE_DIR = os.environ.get("CACHE_DIR", "/data")
os.makedirs(CACHE_DIR, exist_ok=True)
RUNTIME_CACHE_FILE  = os.path.join(CACHE_DIR, "runtime_cache.json")
SCHEDULE_CACHE_FILE = os.path.join(CACHE_DIR, "schedule_index.json")

# ====== JSON 유틸 ======
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

# ====== HTTP 세션 (빠른 경로) ======
def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    })
    s.timeout = 12
    return s

# ====== 팀명 정규화 ======
_ALIAS_MAP = {
    "SSG": ["SSG", "SSG랜더스", "SSG Landers", "랜더스"],
    "KIA": ["KIA", "KIA타이거즈", "기아", "KIA Tigers", "타이거즈"],
    "KT":  ["KT", "KT위즈", "kt", "케이티", "KT Wiz", "kt wiz", "위즈"],
    "LG":  ["LG", "LG트윈스", "엘지", "트윈스"],
    "두산": ["두산", "두산베어스", "베어스"],
    "롯데": ["롯데", "롯데자이언츠", "자이언츠"],
    "삼성": ["삼성", "삼성라이온즈", "라이온즈"],
    "NC":  ["NC", "NC다이노스", "엔씨", "다이노스"],
    "키움": ["키움", "키움히어로즈", "히어로즈"],
    "한화": ["한화", "한화이글스", "이글스"],
}
_ALIAS_LOOKUP = {}
for canon, aliases in _ALIAS_MAP.items():
    for a in aliases:
        k = a.strip().lower()
        _ALIAS_LOOKUP[k] = canon
        _ALIAS_LOOKUP[re.sub(r"\s+", "", k)] = canon

_PATTERNS = [
    (re.compile(r"\bk\s*?t\b.*\bwiz\b", re.I), "KT"),
    (re.compile(r"\bkia\b.*\btigers\b", re.I), "KIA"),
]

def normalize_team(name: str) -> str | None:
    if not name:
        return None
    key = name.strip().lower()
    key2 = re.sub(r"\s+", "", key)
    if key in _ALIAS_LOOKUP:  return _ALIAS_LOOKUP[key]
    if key2 in _ALIAS_LOOKUP: return _ALIAS_LOOKUP[key2]
    for pat, canon in _PATTERNS:
        if pat.search(name):
            return canon
    if "위즈" in name: return "KT"
    if "타이거" in name or "기아" in name: return "KIA"
    return name.strip()

# ====== Selenium 드라이버 ======
def make_driver():
    options = Options()
    options.binary_location = os.environ.get("CHROME_BIN", "/usr/bin/google-chrome")
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,1200")
    options.add_argument("--lang=ko-KR")
    options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
    options.page_load_strategy = "eager"
    return webdriver.Chrome(options=options)

# ====== 스케줄: HTTP 우선, 실패 시 Selenium ======
def get_games_for_date_fast(session, date_str):
    cache = get_schedule_cache()
    if date_str in cache:
        return cache[date_str]

    url = f"https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx?gameDate={date_str}"
    games_minimal = []
    try:
        r = session.get(url, timeout=12)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select("li.game-cont") or soup.select("li[class*='game-cont']")
        for li in cards:
            info = extract_match_info_from_card_bs(li)
            if all([info.get("home"), info.get("away"), info.get("g_id"), info.get("g_dt")]):
                games_minimal.append(info)
    except Exception:
        # 폴백: Selenium
        d = make_driver()
        try:
            wait = WebDriverWait(d, 15)
            d.get(url)
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div#contents")))
            time.sleep(0.4)
            soup = BeautifulSoup(d.page_source, "html.parser")
            cards = soup.select("li.game-cont") or soup.select("li[class*='game-cont']")
            for li in cards:
                info = extract_match_info_from_card_bs(li)
                if all([info.get("home"), info.get("away"), info.get("g_id"), info.get("g_dt")]):
                    games_minimal.append(info)
        finally:
            try: d.quit()
            except: pass

    set_schedule_cache_for_date(date_str, games_minimal)
    return games_minimal

def extract_match_info_from_card_bs(card_li):
    # BeautifulSoup Tag 버전
    home_nm = card_li.get("home_nm")
    away_nm = card_li.get("away_nm")
    g_id = card_li.get("g_id")
    g_dt = card_li.get("g_dt")

    if not (home_nm and away_nm):
        home_alt = card_li.select_one(".team.home .emb img")
        away_alt = card_li.select_one(".team.away .emb img")
        if away_alt and not away_nm: away_nm = (away_alt.get("alt") or "").strip() or None
        if home_alt and not home_nm: home_nm = (home_alt.get("alt") or "").strip() or None

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

# ====== 리뷰 런타임: HTTP 우선, 실패 시 Selenium ======
def open_review_and_get_runtime_fast(session, game_id, game_date):
    key = make_runtime_key(game_id, game_date)
    rc = get_runtime_cache()
    hit = rc.get(key)
    if hit and "runtime_min" in hit:
        return hit["runtime_min"]

    # HTTP 시도 (REVIEW 섹션 직접)
    base = f"https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx?gameId={game_id}&gameDate={game_date}&section=REVIEW"
    try:
        r = session.get(base, timeout=12)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        span = soup.select_one("div.record-etc span#txtRunTime")
        if span:
            runtime = span.get_text(strip=True)
            m = re.search(r"(\d{1,2})\s*[:：]\s*(\d{2})", runtime)
            if not m:
                m = re.search(r"(\d{1,2})\s*시간\s*(\d{1,2})\s*분", runtime)
            if m:
                h, mnt = int(m.group(1)), int(m.group(2))
                run_time_min = h * 60 + mnt
                set_runtime_cache(key, run_time_min)
                return run_time_min
    except Exception:
        pass

    # 폴백: Selenium 클릭
    d = make_driver()
    try:
        wait = WebDriverWait(d, 12)
        base2 = f"https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx?gameId={game_id}&gameDate={game_date}"
        d.get(base2)
        try:
            review_tab = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), '리뷰')]")))
            review_tab.click()
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.record-etc")))
        except Exception:
            d.get(base2 + "&section=REVIEW")
            try:
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.record-etc")))
            except Exception:
                pass
        soup = BeautifulSoup(d.page_source, "html.parser")
        span = soup.select_one("div.record-etc span#txtRunTime")
        if span:
            runtime = span.get_text(strip=True)
            m = re.search(r"(\d{1,2})\s*[:：]\s*(\d{2})", runtime)
            if not m:
                m = re.search(r"(\d{1,2})\s*시간\s*(\d{1,2})\s*분", runtime)
            if m:
                h, mnt = int(m.group(1)), int(m.group(2))
                run_time_min = h * 60 + mnt
                set_runtime_cache(key, run_time_min)
                return run_time_min
    finally:
        try: d.quit()
        except: pass

    return None

# ====== 오늘 카드(Selenium) - UI용만 사용 ======
def get_today_cards(driver):
    wait = WebDriverWait(driver, 15)
    today = datetime.today().strftime("%Y%m%d")
    url = f"https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx?gameDate={today}"
    driver.get(url)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div#contents")))
    soup = BeautifulSoup(driver.page_source, "html.parser")
    return soup.select("li.game-cont") or soup.select("li[class*='game-cont']")

def extract_match_info_from_card(card_li):
    # (Selenium 전용 Tag) — 기존과 동일
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
    my_canon = normalize_team(my_team)
    cards = get_today_cards(driver)
    results = []
    for li in cards:
        info = extract_match_info_from_card(li)
        h, a = info["home"], info["away"]
        if not (h and a):
            continue
        if my_canon in {normalize_team(h), normalize_team(a)}:
            rival_raw = h if normalize_team(a) == my_canon else a
            info["rival"] = normalize_team(rival_raw) or rival_raw
            results.append(info)
    return results

# ====== 핵심: START_DATE~어제까지 전체 계산 (빠른 경로 활용) ======
def collect_history_avg_runtime(my_team, rival_set, start_date=START_DATE):
    session = make_session()
    today_minus_1 = (datetime.today() - timedelta(days=1)).strftime("%Y%m%d")

    if "-" in start_date:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    else:
        start_dt = datetime.strptime(start_date, "%Y%m%d")
    dr = pd.date_range(start=start_dt.strftime("%Y%m%d"), end=today_minus_1)  # 전체

    my_canon = normalize_team(my_team)
    rival_canon_set = {normalize_team(r) for r in (rival_set or set())} if rival_set else set()

    run_times = []
    # 1) 날짜별 스케줄은 HTTP로 빠르게
    for dt in dr:
        date_str = dt.strftime("%Y%m%d")
        games = get_games_for_date_fast(session, date_str)
        if not games:
            continue
        # 2) 내 팀 경기만 선별 → 리뷰시간은 HTTP 우선/필요시 Selenium
        for g in games:
            home = normalize_team(g["home"])
            away = normalize_team(g["away"])
            if my_canon not in {home, away}:
                continue
            opponent = home if away == my_canon else away
            if rival_canon_set and opponent not in rival_canon_set:
                continue
            rt = open_review_and_get_runtime_fast(session, g["g_id"], g["g_dt"])
            if rt is not None:
                run_times.append(rt)

    if run_times:
        return round(sum(run_times) / len(run_times), 1), run_times
    return None, []

# ====== 공통 처리 ======
def compute_for_team(team_name):
    if not team_name:
        return dict(
            result="팀을 선택해주세요.",
            avg_time=None, css_class="", msg="",
            selected_team=None, top30=top30, avg_ref=avg_ref, bottom70=bottom70
        )

    d = make_driver()
    try:
        today_matches = find_today_matches_for_team(d, team_name)
    finally:
        try: d.quit()
        except: pass

    if not today_matches:
        return dict(
            result=f"{team_name}의 오늘 경기를 찾지 못했습니다.",
            avg_time=None, css_class="", msg="",
            selected_team=team_name, top30=top30, avg_ref=avg_ref, bottom70=bottom70
        )

    rivals_today = {normalize_team(m["rival"]) for m in today_matches if m.get("rival")}
    rivals_str = ", ".join(sorted(rivals_today)) if rivals_today else "미확인"

    try:
        avg_time, _ = collect_history_avg_runtime(normalize_team(team_name), rivals_today)
    except Exception:
        avg_time = None

    css_class = ""; msg = ""
    if avg_time is not None:
        if avg_time < top30:
            css_class, msg = "fast", "빠르게 끝나는 경기입니다"
        elif avg_time < avg_ref:
            css_class, msg = "normal", "일반적인 경기 소요 시간입니다"
        elif avg_time < bottom70:
            css_class, msg = "bit-long", "조금 긴 편이에요"
        else:
            css_class, msg = "long", "시간 오래 걸리는 매치업입니다"
        result = f"오늘 {team_name}의 상대팀은 {rivals_str}입니다.<br>과거 {team_name} vs {rivals_str} 평균 경기시간: {avg_time}분"
    else:
        result = f"오늘 {team_name}의 상대팀은 {rivals_str}입니다.<br>과거 경기 데이터가 없습니다."

    return dict(
        result=result, avg_time=avg_time, css_class=css_class, msg=msg,
        selected_team=team_name, top30=top30, avg_ref=avg_ref, bottom70=bottom70
    )

# ====== 라우트 ======
@app.route("/", methods=["GET", "POST"])
@app.route("/hour", methods=["GET", "POST"])
def hour_index():
    try:
        team = (request.args.get("myteam") or request.form.get("myteam") or "").strip()
        ctx = compute_for_team(team) if team else dict(
            result=None, avg_time=None, css_class="", msg="",
            selected_team=None, top30=top30, avg_ref=avg_ref, bottom70=bottom70
        )
        return render_template("hour.html", **ctx)
    except Exception as e:
        return f"오류가 발생했습니다: {type(e).__name__}: {str(e)}", 200

# ====== 헬스/캐시 ======
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

@app.route("/selenium/smoke")
def selenium_smoke():
    try:
        d = make_driver()
        d.get("about:blank")
        title = d.title or "blank"
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

if __name__ == "__main__":
    app.run(debug=True, port=5002, use_reloader=False)
