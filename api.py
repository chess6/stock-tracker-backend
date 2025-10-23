from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
from dotenv import load_dotenv
import json
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
    resp = requests.get(url, params=params)
    if resp.status_code != 200:
        return jsonify({'error': 'Failed to fetch intraday prices'}), 500
    intraday = resp.json()
    # Fetch prevClose directly from Tiingo IEX top-of-book endpoint
    topbook_url = f"{TIINGO_BASE_URL}/iex/"
    topbook_params = {'token': TIINGO_API_KEY, 'tickers': ticker}
    topbook_resp = requests.get(topbook_url, params=topbook_params)
    prev_close = None
    if topbook_resp.status_code == 200:
        topbook_data = topbook_resp.json()
        if isinstance(topbook_data, list) and topbook_data:
            prev_close = topbook_data[0].get('prevClose')
    return jsonify({'intraday': intraday, 'prevClose': prev_close})

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
