from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
import pandas as pd

load_dotenv()

app = Flask(__name__)
CORS(app)

TIINGO_API_KEY = os.getenv("TIINGO_API_KEY")
NASDAQ_API_KEY = os.getenv("NASDAQ_API_KEY")
TIINGO_BASE_URL = 'https://api.tiingo.com'

# https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json # Example SEC API for financials
# https://data.nasdaq.com/api/v3/datatables/SHARADAR/SF1?calendardate=2023-12-31&ticker=XOM # Example Nasdaq API for financials

@app.route('/api/search', methods=['GET'])
def search_ticker():
    query = request.args.get('q', '')
    if not query:
        return jsonify([])
    url = f"{TIINGO_BASE_URL}/tiingo/utilities/search"
    params = {'query': query, 'token': TIINGO_API_KEY}
    resp = requests.get(url, params=params)
    return jsonify(resp.json())

@app.route('/api/ticker/<ticker>/intraday', methods=['GET'])
def ticker_intraday(ticker):
    # Fetch intraday prices from Tiingo IEX endpoint, resampled to 4min
    url = f"{TIINGO_BASE_URL}/iex/{ticker}/prices"
    params = {'token': TIINGO_API_KEY, 'resampleFreq': '4min'}
    resp = requests.get(url, params=params, timeout=10)
    if resp.status_code != 200:
        return jsonify({'error': 'Failed to fetch intraday prices'}), 500
    intraday = resp.json()
    return jsonify({'intraday': intraday})

@app.route('/api/ticker/<ticker>/summary', methods=['GET'])
def ticker_summary(ticker):
    # Prices and meta info
    price_url = f"{TIINGO_BASE_URL}/tiingo/daily/{ticker}/prices"
    meta_url = f"{TIINGO_BASE_URL}/tiingo/daily/{ticker}"
    price_params = {'token': TIINGO_API_KEY}
    meta_params = {'token': TIINGO_API_KEY}
    price_resp = requests.get(price_url, params=price_params)
    meta_resp = requests.get(meta_url, params=meta_params)
    prices = price_resp.json() if price_resp.status_code == 200 else []
    meta = meta_resp.json() if meta_resp.status_code == 200 else {}
    return jsonify({
        'prices': prices,
        'meta': meta
    })

@app.route('/api/ticker/<ticker>/news', methods=['GET'])
def ticker_news(ticker):
    news_url = f"{TIINGO_BASE_URL}/tiingo/news"
    news_params = {'tickers': ticker, 'token': TIINGO_API_KEY}
    news_resp = requests.get(news_url, params=news_params)
    return jsonify(news_resp.json())

@app.route('/api/tickers/top', methods=['GET'])
def tickers_top():
    """Batch fetch top-of-book (last) for all requested tickers in one request."""
    tickers = request.args.get('tickers', '')
    if not tickers:
        return jsonify({'quotes': {}})
    tickers_list = [t.strip().upper() for t in tickers.split(',') if t.strip()]
    url = f"{TIINGO_BASE_URL}/iex"
    params = {
        'tickers': ','.join(tickers_list),
        'token': TIINGO_API_KEY
    }
    resp = requests.get(url, params=params)
    if resp.status_code != 200:
        return jsonify({'error': 'Failed to fetch top-of-book prices'}), 500
    data = resp.json()
    quotes = {}
    if isinstance(data, list):
        for item in data:
            try:
                t = (item.get('ticker') or '').upper()
                if not t:
                    continue
                quotes[t] = {
                    'last': item.get('last'),
                    'tngoLast': item.get('tngoLast'),
                    'bidPrice': item.get('bidPrice'),
                    'askPrice': item.get('askPrice'),
                    'timestamp': item.get('lastSaleTimestamp') or item.get('timestamp'),
                    'name': item.get('name') or t,
                    'prevClose': item.get('prevClose'),
                    'open': item.get('open'),
                    'high': item.get('high'),
                    'low': item.get('low'),
                }
            except Exception:
                continue
    return jsonify({'quotes': quotes})

@app.route('/api/tickers/daily-change', methods=['GET'])
def tickers_daily_change():
    """Return prevClose (last business day) and today close for each ticker.
    Uses Tiingo daily prices endpoint per ticker and aggregates results.
    """
    tickers = request.args.get('tickers', '')
    if not tickers:
        return jsonify({'changes': {}})
    tickers_list = [t.strip().upper() for t in tickers.split(',') if t.strip()]
    changes = {}
    today = datetime.utcnow().date()
    # Determine latest and previous business days relative to today
    last_two_bdays = pd.bdate_range(end=today, periods=2)
    if len(last_two_bdays) >= 2:
        prev_bday = last_two_bdays[-2].date()
        latest_bday = last_two_bdays[-1].date()
    elif len(last_two_bdays) == 1:
        prev_bday = last_two_bdays[-1].date() - timedelta(days=1)
        latest_bday = last_two_bdays[-1].date()
    else:
        prev_bday = today - timedelta(days=2)
        latest_bday = today - timedelta(days=1)
    prev_bday_str = prev_bday.strftime('%Y-%m-%d')
    latest_bday_str = latest_bday.strftime('%Y-%m-%d')
    # For meta fields to preserve compat
    today_str = today.strftime('%Y-%m-%d')
    last_bday_meta = pd.bdate_range(end=today - timedelta(days=1), periods=1)
    last_bday_str = (last_bday_meta[-1].date() if len(last_bday_meta) else today - timedelta(days=1)).strftime('%Y-%m-%d')
    for t in tickers_list:
        try:
            daily_url = f"{TIINGO_BASE_URL}/tiingo/daily/{t}/prices"
            params = {
                'token': TIINGO_API_KEY,
                'startDate': prev_bday_str,
                'endDate': latest_bday_str
            }
            r = requests.get(daily_url, params=params, timeout=8)
            prev_close = None
            today_close = None
            if r.status_code == 200:
                arr = r.json() or []
                for row in arr:
                    d = (row.get('date') or '')[:10]
                    if d == prev_bday_str:
                        prev_close = row.get('close')
                    if d == latest_bday_str:
                        today_close = row.get('close')
            # Fallback: if latest day's close not present (holiday corrections), roll latest back one business day
            if today_close is None:
                # Move window back by one additional business day
                three_bdays = pd.bdate_range(end=prev_bday, periods=2)
                if len(three_bdays) >= 2:
                    older_prev = three_bdays[-2].date().strftime('%Y-%m-%d')
                    newer_prev = three_bdays[-1].date().strftime('%Y-%m-%d')
                    r3 = requests.get(
                        daily_url,
                        params={'token': TIINGO_API_KEY, 'startDate': older_prev, 'endDate': newer_prev},
                        timeout=8
                    )
                    if r3.status_code == 200:
                        arr3 = r3.json() or []
                        # assign today_close as newer_prev, prev_close as older_prev if available
                        for row in arr3:
                            d = (row.get('date') or '')[:10]
                            if d == newer_prev:
                                today_close = row.get('close')
                            if d == older_prev:
                                prev_close = prev_close or row.get('close')
            changes[t] = {
                'prevClose': prev_close,
                'todayClose': today_close,
            }
        except Exception:
            changes[t] = {
                'prevClose': None,
                'todayClose': None,
            }
    return jsonify({'changes': changes, 'meta': {
        'lastBusinessDay': last_bday_str,
        'today': today_str
    }})

@app.route('/api/ticker/financials', methods=['GET'])
def tickers_financials():
    # Accept tickers as comma-separated string: /api/ticker/financials?ticker=AAPL,MSFT,GOOG
    tickers = request.args.get('ticker', '')
    gte = request.args.get('gte')
    dimension = request.args.get('dimension')
    mostRecent = request.args.get('mostRecent')
    if not tickers:
        return jsonify({'error': 'No tickers provided'}), 400
    tickers_list = [t.strip().upper() for t in tickers.split(',') if t.strip()]
    nasdaq_url = "https://data.nasdaq.com/api/v3/datatables/SHARADAR/SF1"
    params = {
        'ticker': ','.join(tickers_list),
        'api_key': NASDAQ_API_KEY
    }
    if gte:
        params['calendardate.gte'] = gte
    elif mostRecent and str(mostRecent).lower() in ('true', '1', 'yes'):
        # Use last year's date for calendardate.gte
        today = datetime.utcnow().date()
        last_year = today.replace(year=today.year - 1)
        params['calendardate.gte'] = last_year.strftime('%Y-%m-%d')

    if (dimension):
        params['dimension'] = dimension

    print(params)
    
    resp = requests.get(nasdaq_url, params=params)
    if resp.status_code != 200:
        return jsonify({'error': 'Failed to fetch NASDAQ fundamentals'}), 500
    data = resp.json()
    # Parse NASDAQ response for all tickers
    rows = data.get('datatable', {}).get('data', [])
    columns = data.get('datatable', {}).get('columns', [])
    col_idx = {col['name']: idx for idx, col in enumerate(columns)}
    # Group rows by ticker, pick latest row for each ticker
    ticker_latest = {}
    for row in rows:
        ticker = row[col_idx.get('ticker')]
        date = row[col_idx.get('calendardate')]
        # Only keep the latest date per ticker
        if ticker not in ticker_latest or (date and date > ticker_latest[ticker]['date']):
            ticker_latest[ticker] = {'row': row, 'date': date}
    def safe_div(num, denom):
        try:
            if num is None or denom in (None, 0):
                return None
            return num / denom
        except Exception:
            return None
    results = {}
    for ticker, info in ticker_latest.items():
        latest = info['row']
        def colval(name):
            idx = col_idx.get(name)
            return latest[idx] if idx is not None and idx < len(latest) else None
        # Try to calculate missing metrics
        bookPerShare = colval('bvps')
        if bookPerShare is None:
            equity = colval('equity')
            sharesbas = colval('sharesbas')
            bookPerShare = safe_div(equity, sharesbas)
        tangibleBookPerShare = colval('tbvps')
        if tangibleBookPerShare is None:
            equity = colval('equity')
            intangibles = colval('intangibles')
            sharesbas = colval('sharesbas')
            tangibleBookPerShare = safe_div((equity - intangibles) if equity is not None and intangibles is not None else None, sharesbas)
        salesPerShare = colval('sps')
        if salesPerShare is None:
            revenue = colval('revenue')
            sharesbas = colval('sharesbas')
            salesPerShare = safe_div(revenue, sharesbas)
        cashFlowOpsPerShare = colval('cfops')
        if cashFlowOpsPerShare is None:
            ncfo = colval('ncfo')
            sharesbas = colval('sharesbas')
            cashFlowOpsPerShare = safe_div(ncfo, sharesbas)
        sfcfPerShare = colval('sfcfps')
        if sfcfPerShare is None:
            fcf = colval('fcf')
            sharesbas = colval('sharesbas')
            sfcfPerShare = safe_div(fcf, sharesbas)
        ebitdaToEv = colval('ebitdaev')
        if ebitdaToEv is None:
            ebitda = colval('ebitda')
            ev = colval('ev')
            ebitdaToEv = safe_div(ebitda, ev)
        metrics = {
            'marketCap': colval('marketcap'),
            'sp': salesPerShare,
            'ebitdaEv': ebitdaToEv,
            'tbp': tangibleBookPerShare,
            'bp': bookPerShare,
            'ep': colval('eps'),
            'cfop': cashFlowOpsPerShare,
            'sfcfp': sfcfPerShare
        }
        results[ticker] = metrics
    return jsonify({'metrics': results, 'raw': data})

### Mock endpoints for development
# @app.route('/api/ticker/<ticker>/intraday', methods=['GET'])
# def ticker_intraday(ticker):
#     filename = "mock/intraday.json"
#     try:
#         with open(filename, 'r') as f:
#             mock_data = json.load(f)
#         return mock_data
#     except FileNotFoundError:
#         print(f"Error: The file '{filename}' was not found.")
#         return None
#     except json.JSONDecodeError:
#         print(f"Error: Could not decode JSON from '{filename}'.")
#         return None

# @app.route('/api/ticker/<ticker>/summary', methods=['GET'])
# def ticker_summary(ticker):
#     # Mock response for development
#     mock_data = {
#         "prices": [
#             {
#                 "adjClose": 249.34,
#                 "adjHigh": 251.82,
#                 "adjLow": 247.47,
#                 "adjOpen": 249.485,
#                 "adjVolume": 33893611,
#                 "close": 249.34,
#                 "date": "2025-10-15T00:00:00+00:00",
#                 "divCash": {"source": "0.0", "parsedValue": 0},
#                 "high": 251.82,
#                 "low": 247.47,
#                 "open": 249.485,
#                 "splitFactor": {"source": "1.0", "parsedValue": 1},
#                 "volume": 33893611
#             }
#         ]
#     }
#     return jsonify(mock_data)

if __name__ == '__main__':
    app.run(debug=True)
