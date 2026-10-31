import os, re, time, html, threading
from concurrent.futures import ThreadPoolExecutor
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from flask import Flask, render_template, jsonify
import requests

app = Flask(__name__)
app.json.ensure_ascii = False
TTL = int(os.environ.get("CACHE_SECONDS", "300"))
_lock = threading.Lock()

# ── 언론사 (RSS 주소는 각 사 정책에 따라 바뀔 수 있으니 접속 확인 필요) ──
SOURCES = {  # id: (이름, RSS, 속보점수, 지역, 국기)
    "bloomberg":   ("Bloomberg", "https://feeds.bloomberg.com/markets/news.rss", 9.9, "us", "🇺🇸"),
    "wsj":         ("WSJ", "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain", 9.3, "us", "🇺🇸"),
    "cnbc":        ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html", 9.5, "us", "🇺🇸"),
    "marketwatch": ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories", 9.4, "us", "🇺🇸"),
    "yahoo":       ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex", 9.0, "us", "🇺🇸"),
    "seekingalpha":("Seeking Alpha", "https://seekingalpha.com/market_currents.xml", 9.0, "us", "🇺🇸"),
    "reuters":     ("Reuters", "", 9.8, "us", "🇺🇸"),  # 공식 무료 RSS 없음
    # 아시아 4 (아시아·중국·홍콩·일본)
    "cna":         ("CNA 아시아", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6511", None, "asia", "🇸🇬"),
    "chinadaily":  ("China Daily 중국", "https://www.chinadaily.com.cn/rss/business_rss.xml", None, "asia", "🇨🇳"),
    "scmp":        ("SCMP 홍콩", "https://www.scmp.com/rss/92/feed", None, "asia", "🇭🇰"),
    "nikkei":      ("Nikkei Asia 일본", "https://asia.nikkei.com/rss/feed/nar", None, "asia", "🇯🇵"),
    # 유럽 5 (나라별 1개)
    "bbc":         ("BBC 영국", "https://feeds.bbci.co.uk/news/business/rss.xml", None, "eu", "🇬🇧"),
    "france24":    ("France 24 프랑스", "https://www.france24.com/en/business/rss", None, "eu", "🇫🇷"),
    "dw":          ("DW 독일", "https://rss.dw.com/xml/rss-en-bus", None, "eu", "🇩🇪"),
    "elpais":      ("El País 스페인", "https://feeds.elpais.com/mrss-s/pages/ep/site/english.elpais.com/portada", None, "eu", "🇪🇸"),
    "ansa":        ("ANSA 이탈리아", "https://www.ansa.it/english/news/english_nr_rss.xml", None, "eu", "🇮🇹"),
    # 중동 3 (나라별 1개)
    "arabnews":    ("Arab News 사우디", "https://www.arabnews.com/rss.xml", None, "me", "🇸🇦"),
    "aljazeera":   ("Al Jazeera 카타르", "https://www.aljazeera.com/xml/rss/all.xml", None, "me", "🇶🇦"),
    "thenational": ("The National UAE", "https://www.thenationalnews.com/arc/outboundfeeds/rss/category/business/?outputType=xml", None, "me", "🇦🇪"),
}

# ── 테마 / 주체 / 심리 키워드 규칙 ──
THEMES = {
    "AI·반도체":   r"\bai\b|nvidia|chip|semiconductor|amd|openai|data ?center|gpu|tsmc",
    "연준·금리":   r"\bfed\b|federal reserve|rate (cut|hike)|fomc|powell|inflation|cpi|pce|treasury|yield",
    "실적":        r"earnings|revenue|guidance|profit|eps|quarter|forecast",
    "에너지·원자재": r"oil|crude|opec|gas|lng|aramco|adnoc|gold|copper|commodit",
    "지정학·무역": r"tariff|china|sanction|war|trade|geopolit|russia|middle east",
    "전기차·모빌리티": r"tesla|\bev\b|electric vehicle|robotaxi|autonomous|ford|gm\b",
    "금융·은행":   r"bank|jpmorgan|goldman|credit|loan|lender",
    "바이오·헬스": r"pharma|drug|fda|biotech|health|vaccine",
}
ACTORS = {
    "연준(Fed)":        r"\bfed\b|federal reserve|powell|fomc",
    "미 정부·정책":     r"white house|trump|congress|treasury dept|tariff|regulator|sec\b|ftc|doj",
    "빅테크·기업":      r"apple|microsoft|nvidia|amazon|google|alphabet|meta\b|tesla|amd|earnings",
    "월가 기관·애널":   r"analyst|upgrade|downgrade|price target|goldman|morgan stanley|jpmorgan|hedge fund",
    "해외 중앙은행":    r"ecb|boj|bank of japan|bank of england|bundesbank|pboc|bank of korea|central bank",
    "중국·지정학":      r"china|beijing|hong kong|hang seng|yuan|taiwan|russia|ukraine|middle east|gaza|iran|sanction",
    "원유·원자재":      r"oil|crude|opec|gold|copper|aramco|adnoc|lng",
    "개인·ETF 자금":    r"retail investor|etf|fund flow|inflow|outflow|options",
}
BULL = r"surge|rally|beat|record|raise|upgrade|jump|gain|soar|approve|cut rates|rate cut|strong|rebound|top"
BEAR = r"fall|plunge|miss|downgrade|slump|drop|warn|probe|lawsuit|ban|tariff|recession|cut forecast|weak|selloff|sink|fear"
TICKERS = r"\b(NVDA|AAPL|MSFT|TSLA|AMZN|GOOGL?|META|AMD|JPM|XOM|INTC|AVGO)\b"
NAMES = {"nvidia":"NVDA","apple":"AAPL","microsoft":"MSFT","tesla":"TSLA","amazon":"AMZN","alphabet":"GOOGL","google":"GOOGL","meta":"META","amd":"AMD","jpmorgan":"JPM","exxon":"XOM","intel":"INTC","broadcom":"AVGO"}

def classify(title, summary=""):
    t = f"{title} {summary}".lower()
    theme = next((k for k, p in THEMES.items() if re.search(p, t)), "기타")
    actors = [k for k, p in ACTORS.items() if re.search(p, t)] or ["빅테크·기업"]
    b, r = len(re.findall(BULL, t)), len(re.findall(BEAR, t))
    sent = "Bullish" if b > r else "Bearish" if r > b else "Neutral"
    m = re.search(TICKERS, title)
    ticker = m.group(1) if m else next((v for k, v in NAMES.items() if k in t), "MARKET")
    impact = min(10, round(5 + 1.2 * (b + r) + (1 if len(actors) > 1 else 0), 1))
    return theme, actors, sent, ticker, impact

# ── 샘플(오프라인/RSS 실패 시). 실제 보도가 아닌 UI 확인용 예시입니다. ──
S = [  # src, ticker, theme, sent, impact, actors, en, ko, 영향분석
 ("bloomberg","NVDA","AI·반도체","Bullish",9.6,["빅테크·기업","월가 기관·애널"],"Chipmaker raises data-center outlook on surging AI GPU orders","AI 칩 업체, GPU 주문 급증에 데이터센터 전망 상향","AI 인프라 투자 지속 기대 → 반도체·전력주 동반 강세 요인"),
 ("bloomberg","MACRO","연준·금리","Bullish",9.0,["연준(Fed)","원유·원자재"],"Treasury yields slip as traders price deeper rate cuts","금리 인하 폭 확대 베팅에 美 국채금리 하락","성장주·소형주 밸류에이션 부담 완화"),
 ("reuters","AAPL","AI·반도체","Neutral",7.4,["빅테크·기업","중국·지정학"],"Apple suppliers expand output in India and Southeast Asia","애플 협력사, 인도·동남아 생산 확대","공급망 다변화는 중장기 호재, 단기 비용 부담"),
 ("reuters","MACRO","지정학·무역","Bearish",8.3,["미 정부·정책","중국·지정학"],"New tariff threat on imports rattles global supply chains","신규 관세 위협에 글로벌 공급망 불안","수입 의존 소비재·제조업 마진 압박, 변동성 확대"),
 ("wsj","MACRO","연준·금리","Neutral",8.8,["연준(Fed)"],"Fed officials split over pace of further easing","연준 인사들, 추가 완화 속도 놓고 의견 갈려","금리 경로 불확실성 → 지수 횡보 가능"),
 ("wsj","JPM","금융·은행","Bullish",7.6,["월가 기관·애널","개인·ETF 자금"],"Big banks report stronger trading and lending income","대형은행, 트레이딩·대출 수익 개선","금융 섹터 순환매 유입 요인"),
 ("cnbc","TSLA","전기차·모빌리티","Bullish",8.7,["미 정부·정책","빅테크·기업"],"Robotaxi pilot gets regulatory green light, shares jump","로보택시 시범운행 허가, 주가 급등","자율주행 모멘텀, 단 규제 변수 상존"),
 ("cnbc","XOM","에너지·원자재","Bearish",7.9,["원유·원자재","해외 중앙은행"],"Oil slumps as demand worries and supply outlook weigh","수요 우려·공급 전망에 유가 급락","에너지주 약세, 인플레 압력 완화로 금리엔 호재"),
 ("marketwatch","AMZN","실적","Bullish",8.1,["빅테크·기업","월가 기관·애널"],"AWS growth tops estimates on enterprise AI workloads","AWS 성장률 예상 상회, 기업 AI 수요","클라우드 3사 동반 상향 기대"),
 ("marketwatch","MARKET","실적","Bearish",7.2,["빅테크·기업","개인·ETF 자금"],"Retailers warn on holiday demand as consumers pull back","소매업체, 연말 수요 둔화 경고","소비 둔화 우려 → 경기민감 소비재 하락"),
 ("yahoo","MSFT","AI·반도체","Bullish",8.0,["빅테크·기업"],"Microsoft expands AI datacenter capacity with new partners","MS, 신규 파트너와 AI 데이터센터 증설","Azure 성장 가속 기대"),
 ("yahoo","META","바이오·헬스","Neutral",5.8,["미 정부·정책"],"Regulators open review of drug pricing proposals","규제당국, 약가 정책안 검토 착수","제약·바이오 정책 불확실성"),
 ("seekingalpha","AMD","AI·반도체","Bullish",8.2,["월가 기관·애널"],"AMD accelerator benchmarks beat expectations in tests","AMD 가속기, 벤치마크서 기대치 상회","엔비디아 독점 완화 기대"),
 ("seekingalpha","INTC","AI·반도체","Bearish",7.0,["월가 기관·애널"],"Analyst downgrades chipmaker on weak foundry outlook","애널리스트, 파운드리 부진에 투자의견 하향","반도체 내 종목 차별화 심화"),
 ("reuters","MARKET","지정학·무역","Bearish",8.0,["중국·지정학","원유·원자재"],"Middle East tensions lift safe-haven demand, stocks slip","중동 긴장에 안전자산 선호, 증시 약세","방산·금·달러 강세, 위험자산 약세"),
 ("wsj","MARKET","연준·금리","Bullish",7.5,["해외 중앙은행","개인·ETF 자금"],"Global central banks signal easing; equity ETFs see inflows","주요 중앙은행 완화 시그널, 주식 ETF 자금 유입","글로벌 유동성 개선 기대"),

 ("cna","STI","지정학·무역","Neutral",6.5,["해외 중앙은행"],"Asian markets mixed as investors await regional central bank signals","아시아 증시, 지역 중앙은행 신호 앞두고 혼조","금리 경로 확인 전까지 관망세"),
 ("chinadaily","SHCOMP","지정학·무역","Bullish",7.3,["중국·지정학","미 정부·정책"],"Stimulus hopes lift China consumer and tech shares","경기부양 기대에 중국 소비·기술주 강세","중국 노출 큰 미 기업(애플·테슬라)에 간접 호재"),
 ("scmp","HSI","금융·은행","Bearish",7.1,["중국·지정학","개인·ETF 자금"],"Hong Kong stocks slip on property-sector worries","홍콩 증시, 부동산 업황 우려에 하락","중화권 위험 회피 → 아시아 신흥국 변동성 확대"),
 ("nikkei","N225","AI·반도체","Bullish",7.8,["월가 기관·애널","해외 중앙은행"],"Japan chip-equipment makers rally on strong AI demand","일본 반도체 장비주, AI 수요에 강세","AI 반도체 공급망 전반 호재"),
 ("bbc","FTSE","연준·금리","Neutral",6.8,["해외 중앙은행"],"UK inflation steadies, easing path stays uncertain","영국 물가 안정세, 완화 경로는 불확실","유럽 금리 기대 변화는 달러·미 국채에 영향"),
 ("france24","CAC","실적","Bullish",6.9,["빅테크·기업"],"French luxury and aerospace names post solid results","프랑스 명품·항공우주 기업 견조한 실적","유럽 소비·산업 회복 신호"),
 ("dw","DAX","에너지·원자재","Bearish",7.4,["원유·원자재","해외 중앙은행"],"German industry warns on energy costs and weak orders","독일 산업계, 에너지 비용·수주 부진 경고","유럽 제조업 둔화 → 글로벌 경기 우려"),
 ("elpais","IBEX","금융·은행","Bullish",6.4,["월가 기관·애널"],"Spanish banks lead gains on higher lending margins","스페인 은행주, 대출 마진 개선에 상승","유럽 금융 섹터 강세 흐름"),
 ("ansa","FTSEMIB","금융·은행","Neutral",6.0,["해외 중앙은행","미 정부·정책"],"Italy markets steady ahead of budget and ECB commentary","이탈리아 증시, 예산안·ECB 발언 앞두고 보합","국채 스프레드가 변수"),
 ("arabnews","TASI","에너지·원자재","Bearish",7.7,["원유·원자재"],"Saudi market eases as crude prices soften","사우디 증시, 유가 약세에 하락","산유국 재정·에너지주 약세, 인플레엔 호재"),
 ("aljazeera","QE","에너지·원자재","Bullish",7.2,["원유·원자재","개인·ETF 자금"],"Qatar LNG demand outlook supports regional equities","카타르 LNG 수요 전망, 지역 증시 지지","LNG 가격 상승은 글로벌 에너지 비용 요인"),
 ("thenational","ADX","지정학·무역","Neutral",6.6,["중국·지정학","원유·원자재"],"Gulf markets watch regional tensions and oil prices","걸프 증시, 지정학 긴장과 유가 주시","중동 리스크는 유가·안전자산 수요로 전이"),
]

_cache = {"t": 0, "data": None}

def fetch_feed(sid):
    url = SOURCES[sid][1]
    if not url: return []
    root = ET.fromstring(requests.get(url, timeout=6, headers={"User-Agent": "Mozilla/5.0"}).content)
    out = []
    for it in root.iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        desc = re.sub(r"<[^>]+>", "", html.unescape(it.findtext("description") or ""))[:220]
        try: ago = max(0, int((datetime.now(timezone.utc) - parsedate_to_datetime(it.findtext("pubDate"))).total_seconds() // 60))
        except Exception: ago = 0
        if title:
            theme, actors, sent, tk, imp = classify(title, desc)
            out.append(dict(source=sid, ticker=tk, theme=theme, actors=actors, sentiment=sent, impact=imp,
                            title_en=title, title_ko=None, summary=desc, why=None, ago=ago, url=it.findtext("link") or "#"))
        if len(out) >= 12: break
    return out

def build():
    items, errors = [], []
    def safe(sid):
        try: return sid, fetch_feed(sid), True
        except Exception: return sid, [], False
    with ThreadPoolExecutor(8) as ex:
        for sid, got, ok in ex.map(safe, SOURCES):
            items += got
            if not ok: errors.append(sid)
    sample = len(items) < 5
    if sample:
        items = [dict(source=s, ticker=tk, theme=th, actors=ac, sentiment=se, impact=im, title_en=en, title_ko=ko,
                      summary="", why=why, ago=(i * 7) % 90, url="#") for i, (s, tk, th, se, im, ac, en, ko, why) in enumerate(S)]
    items.sort(key=lambda x: (x["ago"], -x["impact"]))
    sign = {"Bullish": 1, "Bearish": -1, "Neutral": 0}
    actors = {a: dict(name=a, net=0, tot=0, n=0, bull=0, bear=0) for a in ACTORS}
    themes, srcs = {}, {k: dict(id=k, name=v[0], speed=v[2], region=v[3], flag=v[4], n=0, bull=0, bear=0) for k, v in SOURCES.items()}
    for x in items:
        sg = sign[x["sentiment"]]
        for a in x["actors"]:
            d = actors[a]; d["net"] += sg * x["impact"]; d["tot"] += x["impact"]; d["n"] += 1
            d["bull"] += sg > 0; d["bear"] += sg < 0
        t = themes.setdefault(x["theme"], dict(name=x["theme"], n=0, net=0)); t["n"] += 1; t["net"] += sg
        s = srcs[x["source"]]; s["n"] += 1; s["bull"] += sg > 0; s["bear"] += sg < 0
    for d in actors.values(): d["score"] = round(100 * d["net"] / d["tot"]) if d["tot"] else 0
    return dict(status="success", is_sample=sample, failed=errors, updated=datetime.now().strftime("%H:%M:%S"),
                items=items, actors=sorted(actors.values(), key=lambda d: -abs(d["score"])),
                themes=sorted(themes.values(), key=lambda d: -d["n"]), sources=list(srcs.values()))

@app.route("/")
def index(): return render_template("index.html")

@app.route("/healthz")
def healthz(): return "ok"

@app.route("/api/news")
def get_news():
    # 최초 1회만 수집을 기다리고, 이후엔 캐시를 즉시 반환 + 만료되면 백그라운드 갱신
    if _cache["data"] is None:
        with _lock:
            if _cache["data"] is None: _cache.update(t=time.time(), data=build())
    elif time.time() - _cache["t"] > TTL and _lock.acquire(blocking=False):
        def bg():
            try: _cache.update(t=time.time(), data=build())
            finally: _lock.release()
        threading.Thread(target=bg, daemon=True).start()
    r = jsonify(_cache["data"]); r.headers["Cache-Control"] = "public, max-age=60"
    return r

if __name__ == "__main__":  # 로컬 개발용. 운영은 gunicorn(Procfile) 사용
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("FLASK_DEBUG") == "1")
