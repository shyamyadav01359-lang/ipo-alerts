# ipo_alerts_auto.py
"""
Auto-fetch IPOs (tries FMP -> Finnhub -> fallback scraping NSE/BSE),
then checks today's listings, calculates gain (via yfinance), and sends Telegram alert
if gain is within MIN_GAIN..MAX_GAIN.

Requirements (installed in workflow): pandas, requests, yfinance, beautifulsoup4, lxml
"""
import os
import requests
import pandas as pd
import datetime
import yfinance as yf
from bs4 import BeautifulSoup
import time

# config from env / secrets
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MIN_GAIN = float(os.getenv("MIN_GAIN", "8"))
MAX_GAIN = float(os.getenv("MAX_GAIN", "30"))

FMP_API_KEY = os.getenv("FMP_API_KEY")        # optional: FinancialModelingPrep API key
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")# optional: Finnhub API key

IPOS_CSV = "ipos.csv"   # fallback storage (we still keep for reference)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram token or chat id not set.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message})
    print("Telegram send status:", resp.status_code)
    return resp.status_code == 200

def get_ltp(symbol, exchange):
    # yfinance ticker mapping
    if exchange.upper() == "NSE":
        ticker = f"{symbol}.NS"
    elif exchange.upper() == "BSE":
        ticker = f"{symbol}.BO"
    else:
        ticker = symbol
    try:
        tk = yf.Ticker(ticker)
        df = tk.history(period="1d", interval="1m")
        if df.empty:
            return None
        return float(df['Close'].iloc[-1])
    except Exception as e:
        print("yfinance error for", ticker, e)
        return None

# --------------- Fetch methods ---------------

def fetch_fmp_ipos():
    """Use FinancialModelingPrep IPO calendar (if API key provided)."""
    if not FMP_API_KEY:
        return []
    url = f"https://financialmodelingprep.com/api/v3/ipo_calendar?apikey={FMP_API_KEY}"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        results = []
        for item in data:
            # FMP fields: date, company, symbol, price
            date_str = item.get("date") or item.get("dateIPO") or item.get("date")
            # Try to normalise format to YYYY-MM-DD
            try:
                dt = pd.to_datetime(date_str).date()
            except:
                continue
            symbol = item.get("symbol") or ""
            price = item.get("price") or item.get("priceFrom") or item.get("priceTo") or None
            results.append({"symbol": symbol, "exchange": "NSE", "issue_price": price, "listing_date": dt})
        print("FMP returned", len(results), "items")
        return results
    except Exception as e:
        print("FMP fetch error:", e)
        return []

def fetch_finnhub_ipos():
    """Use Finnhub IPO calendar (if key provided)."""
    if not FINNHUB_API_KEY:
        return []
    url = f"https://finnhub.io/api/v1/calendar/ipo?from={datetime.date.today()}&to={datetime.date.today()}&token={FINNHUB_API_KEY}"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        # Finnhub returns {'ipoCalendar': [...]} or { 'data': [...] } depending on plan
        items = data.get('ipoCalendar') or data.get('data') or data
        results = []
        for item in items:
            date_str = item.get('date') or item.get('startDate') or item.get('offerDate')
            try:
                dt = pd.to_datetime(date_str).date()
            except:
                continue
            symbol = item.get('symbol') or item.get('ticker') or ""
            price = item.get('price') or item.get('priceFrom') or None
            results.append({"symbol": symbol, "exchange": "NSE", "issue_price": price, "listing_date": dt})
        print("Finnhub returned", len(results), "items")
        return results
    except Exception as e:
        print("Finnhub fetch error:", e)
        return []

def scrape_nse_upcoming():
    """Attempt to scrape NSE upcoming issues page for listings.
    NOTE: NSE sometimes blocks direct scraping; this is a best-effort fallback."""
    url = "https://www.nseindia.com/market-data/all-upcoming-issues-ipo"
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html"}
    try:
        s = requests.Session()
        s.headers.update(headers)
        # first request to get cookies
        s.get("https://www.nseindia.com", timeout=10)
        r = s.get(url, timeout=15)
        soup = BeautifulSoup(r.text, "lxml")
        results = []
        # Try to find table rows (structure may change)
        table = soup.find("table")
        if not table:
            return results
        rows = table.find_all("tr")
        for tr in rows[1:]:
            cols = [c.get_text(strip=True) for c in tr.find_all("td")]
            if len(cols) < 4:
                continue
            name = cols[0]
            symbol = cols[1].split()[0] if cols[1] else ""
            issue_price = None
            # listing date often in one of the columns; try parse
            try:
                listing_date = pd.to_datetime(cols[3]).date()
            except:
                listing_date = None
            results.append({"symbol": symbol, "exchange": "NSE", "issue_price": issue_price, "listing_date": listing_date})
        print("NSE scrape returned", len(results))
        return results
    except Exception as e:
        print("NSE scrape error:", e)
        return []

def scrape_bse_public_issues():
    """Attempt a basic fetch from BSE public issues page (best-effort fallback)."""
    url = "https://www.bseindia.com/markets/PublicIssues/frmPublicIssues.aspx"
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, "lxml")
        results = []
        # BSE page structure is complex; search for links/tables
        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            for tr in rows[1:]:
                cols = [c.get_text(strip=True) for c in tr.find_all("td")]
                if not cols:
                    continue
                # best effort parse
                symbol = cols[0].split()[0] if cols else ""
                try:
                    listing_date = pd.to_datetime(cols[-1]).date()
                except:
                    listing_date = None
                results.append({"symbol": symbol, "exchange": "BSE", "issue_price": None, "listing_date": listing_date})
        print("BSE scrape returned", len(results))
        return results
    except Exception as e:
        print("BSE scrape error:", e)
        return []

# --------------- Main logic ---------------

def collect_ipos():
    # Try FMP
    ipos = []
    if FMP_API_KEY:
        ipos = fetch_fmp_ipos()
    if not ipos and FINNHUB_API_KEY:
        ipos = fetch_finnhub_ipos()
    if not ipos:
        # fallback: try scraping
        ipos = scrape_nse_upcoming() + scrape_bse_public_issues()
    # normalize to DataFrame
    if not ipos:
        print("No IPO data found from any source.")
        return pd.DataFrame(columns=['symbol','exchange','issue_price','listing_date'])
    df = pd.DataFrame(ipos)
    # keep only rows with a date
    df = df.dropna(subset=['listing_date'])
    # ensure listing_date is date
    df['listing_date'] = pd.to_datetime(df['listing_date']).dt.date
    return df

def main():
    today = datetime.date.today()
    print("Running IPO auto-check for", today)
    df = collect_ipos()
    print("Total IPOs found:", len(df))
    # Save to ipos.csv for reference
    if not df.empty:
        df.to_csv(IPOS_CSV, index=False)
    df_today = df[df['listing_date'] == today]
    if df_today.empty:
        print("No IPOs listing today (according to data sources).")
        return
    for _, row in df_today.iterrows():
        sym = str(row.get('symbol','')).strip()
        exch = str(row.get('exchange','NSE')).strip() or "NSE"
        issue = row.get('issue_price')
        # if issue price missing, try to fallback or skip
        if issue is None or pd.isna(issue):
            print(sym, "issue_price missing; trying to proceed (will compute gain with LTP vs issue if you want)")
            # Optionally skip if no issue price
            # continue
            # For now treat issue as same as LTP to avoid division by zero — but better skip
            issue = None
        # get LTP
        ltp = None
        # If there's a test_ltp field in the dataframe (rare) use it
        if 'test_ltp' in row and not pd.isna(row['test_ltp']):
            ltp = float(row['test_ltp'])
        else:
            # try get_ltp with a short retry
            for attempt in range(2):
                ltp = get_ltp(sym, exch)
                if ltp is not None:
                    break
                time.sleep(1)
        if ltp is None:
            print("LTP not found for", sym, "- skipping")
            continue
        if issue is None:
            print(f"Issue price missing for {sym}; cannot compute gain. Skipping alert.")
            continue
        try:
            issue = float(issue)
            gain = (ltp - issue)/issue * 100.0
        except Exception as e:
            print("Calculation error for", sym, e)
            continue
        print(sym, exch, "issue", issue, "ltp", ltp, "gain", round(gain,2))
        if MIN_GAIN <= gain <= MAX_GAIN:
            msg = f"🔔 IPO Alert: {sym} ({exch})\nIssue ₹{issue:.2f} LTP ₹{ltp:.2f}\nGain {gain:.2f}%"
            send_telegram(msg)

if __name__ == "__main__":
    main()
