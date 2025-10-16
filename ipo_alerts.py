# ipo_alerts.py
import os, pandas as pd, datetime, yfinance as yf, requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
IPOS_CSV = "ipos.csv"
MIN_GAIN = float(os.getenv("MIN_GAIN","8"))
MAX_GAIN = float(os.getenv("MAX_GAIN","30"))

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Token/chat missing"); return
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                      data={"chat_id":TELEGRAM_CHAT_ID,"text":msg})
    print("Telegram status", r.status_code)

def get_ltp(sym, exch):
    t = f"{sym}.NS" if exch.upper()=="NSE" else f"{sym}.BO" if exch.upper()=="BSE" else sym
    tk = yf.Ticker(t)
    data = tk.history(period="1d", interval="1m")
    if data.empty: return None
    return float(data['Close'].iloc[-1])

def main():
    today = datetime.date.today()
    if not os.path.exists(IPOS_CSV):
        print("ipos.csv not found"); return
    df = pd.read_csv(IPOS_CSV, parse_dates=['listing_date'])
    df_today = df[df['listing_date'].dt.date == today]
    if df_today.empty:
        print("No IPOs listing today.")
        return
    for _, r in df_today.iterrows():
        sym, exch, issue = str(r['symbol']).strip(), str(r['exchange']).strip(), float(r['issue_price'])
        ltp = float(r['test_ltp']) if 'test_ltp' in r and not pd.isna(r['test_ltp']) else get_ltp(sym, exch)
        if ltp is None:
            print(sym, "LTP not found"); continue
        gain = (ltp - issue)/issue*100
        print(sym, "gain", round(gain,2))
        if MIN_GAIN <= gain <= MAX_GAIN:
            msg = f"🔔 IPO Alert: {sym} ({exch})\nIssue ₹{issue:.2f} LTP ₹{ltp:.2f}\nGain {gain:.2f}%"
            send_telegram(msg)

if __name__=="__main__":
    main()
