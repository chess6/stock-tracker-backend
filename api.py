from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
import pandas as pd
import pandas_market_calendars as mcal
import json
from dateutil.relativedelta import relativedelta

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

    # Fetch ticker metadata from NASDAQ SHARADAR/TICKERS
    nasdaq_url = "https://data.nasdaq.com/api/v3/datatables/SHARADAR/TICKERS"
    nasdaq_params = {
        'ticker': ticker.upper(),
        'api_key': NASDAQ_API_KEY,
        'table': 'SF1'
    }
    meta_resp = requests.get(nasdaq_url, params=nasdaq_params, timeout=10)
    tickerMeta = None
    if meta_resp.status_code == 200:
        meta_data = meta_resp.json()
        datatable = meta_data.get('datatable', {})
        columns = datatable.get('columns', [])
        data = datatable.get('data', [])
        if data and columns:
            col_names = [col['name'] for col in columns]
            # Use first row (should be only one for ticker)
            row = data[0]
            tickerMeta = {col_names[i]: row[i] for i in range(len(col_names))}
    return jsonify({'intraday': intraday, 'tickerMeta': tickerMeta})

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
    Uses Nasdaq Data Link SHARADAR/SEP batch endpoint for daily closes, falls back to Tiingo if needed.
    """
    tickers = request.args.get('tickers', '')
    if not tickers:
        return jsonify({'changes': {}})
    tickers_list = [t.strip().upper() for t in tickers.split(',') if t.strip()]
    changes = {}
    today = datetime.utcnow().date()
    # Determine last two valid market close dates using pandas_market_calendars
    nyse = mcal.get_calendar('NYSE')
    schedule = nyse.schedule(start_date=(today - timedelta(days=10)), end_date=today)
    close_dates = list(schedule.index.strftime('%Y-%m-%d'))
    prev_bday_str = close_dates[-2]
    latest_bday_str = close_dates[-1]
    print(f"Determined market close days - Previous: {prev_bday_str}, Latest: {latest_bday_str}")
    today_str = today.strftime('%Y-%m-%d')
    last_bday_str = prev_bday_str
    # Try Nasdaq Data Link batch fetch first
    nasdaq_url = "https://data.nasdaq.com/api/v3/datatables/SHARADAR/SEP"
    params = {
        'ticker': ','.join(tickers_list),
        'date': f"{prev_bday_str},{latest_bday_str}",
        'api_key': NASDAQ_API_KEY
    }
    resp = requests.get(nasdaq_url, params=params, timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        rows = data.get('datatable', {}).get('data', [])
        columns = data.get('datatable', {}).get('columns', [])
        col_idx = {col['name']: idx for idx, col in enumerate(columns)}
        # Build changes dict from batch response
        for t in tickers_list:
            prev_close = None
            today_close = None
            for row in rows:
                ticker = row[col_idx.get('ticker')]
                date = row[col_idx.get('date')]
                close = row[col_idx.get('close')]
                if ticker == t:
                    if date == prev_bday_str:
                        prev_close = close
                    if date == latest_bday_str:
                        today_close = close
            changes[t] = {
                'prevClose': prev_close,
                'todayClose': today_close,
            }
        return jsonify({'changes': changes, 'meta': {
            'lastBusinessDay': last_bday_str,
            'today': today_str
        }})
    else:
        return jsonify({'error': 'Failed to fetch Nasdaq daily closes', 'status_code': resp.status_code}), 500

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

    query_str = '&'.join(f'{k}={v}' for k, v in params.items())
    print(f"Fetching NASDAQ financials: {nasdaq_url}?{query_str}")
    
    resp = requests.get(nasdaq_url, params=params)
    if resp.status_code != 200:
        return jsonify({'error': 'Failed to fetch NASDAQ fundamentals'}), 500
    data = resp.json()
    # Parse NASDAQ response for all tickers
    rows = data.get('datatable', {}).get('data', [])
    columns = data.get('datatable', {}).get('columns', [])
    col_idx = {col['name']: idx for idx, col in enumerate(columns)}
    # Group rows by (ticker, calendardate, dimension), keep only the row with latest lastupdated
    latest_rows = {}
    for row in rows:
        ticker = row[col_idx.get('ticker')]
        calendardate = row[col_idx.get('calendardate')]
        dimension = row[col_idx.get('dimension')]
        lastupdated = row[col_idx.get('lastupdated')]
        key = (ticker, calendardate, dimension)
        if key not in latest_rows or (lastupdated and lastupdated > latest_rows[key]['lastupdated']):
            latest_rows[key] = {'row': row, 'lastupdated': lastupdated}
    def safe_div(num, denom):
        try:
            if num is None or denom in (None, 0):
                return None
            return num / denom
        except Exception:
            return None
    # Build a deduped list of rows for the raw datatable response
    deduped_rows = [info['row'] for info in latest_rows.values()]
    # Sort deterministically: by ticker, calendardate, dimension
    def _sort_key(r):
        # Sort by ticker ASC, calendardate DESC, dimension ASC
        # For descending date, use reverse sort or negative value
        ticker = r[col_idx.get('ticker')]
        calendardate = r[col_idx.get('calendardate')] or ''
        dimension = r[col_idx.get('dimension')] or ''
        return (ticker, -int(calendardate.replace('-', '')) if calendardate else 0, dimension)
    deduped_rows.sort(key=_sort_key)
    # Replace raw datatable data with deduped rows so frontend sees clean data
    if isinstance(data, dict) and 'datatable' in data and isinstance(data['datatable'], dict):
        before = len(data['datatable'].get('data', []) or [])
        data['datatable']['data'] = deduped_rows
        after = len(deduped_rows)
        print(f"Deduped NASDAQ SF1 rows: {before} -> {after}")

    results = {}
    for key, info in latest_rows.items():
        ticker, calendardate, dimension = key
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
        # Use ticker as key, but you could also use (ticker, calendardate, dimension) if needed
        results[ticker] = metrics
    return jsonify({'metrics': results, 'raw': data})


@app.route('/api/insiders/buying-sums', methods=['GET'])
def insiders_buying_sums():
    """
    Aggregate insider buying (transactionvalue) over the last 6/3/1 months by ticker.
    - Accepts optional query param 'tickers' (comma-separated) to limit aggregation.
    - Uses SHARADAR/SF2 with securityadcode=NA,DA (acquisitions) and filingdate.gte last 6 months.
    - Sums positive transactionvalue by ticker for windows: 6M, 3M, 1M based on transactiondate (fallback filingdate).
    Response: { rows: [ { ticker, company, buy6m, buy3m, buy1m }, ... ] }
    """
    tickers_param = request.args.get('tickers', '')
    tickers_list = [t.strip().upper() for t in tickers_param.split(',') if t.strip()]
    today = datetime.utcnow().date()
    six_months_ago = (today - relativedelta(months=6))
    three_months_ago = (today - relativedelta(months=3))
    one_month_ago = (today - relativedelta(months=1))

    # Helper to parse Data Link datatable shape
    def parse_datatable(payload):
        datatable = payload.get('datatable', {}) if isinstance(payload, dict) else {}
        cols = datatable.get('columns', [])
        data = datatable.get('data', [])
        col_idx = {c['name']: i for i, c in enumerate(cols)}
        return data, col_idx

    # Print actual request URLs for 6m, 3m, 1m
    nasdaq_url = "https://data.nasdaq.com/api/v3/datatables/SHARADAR/SF2"
    params_6m = {
        'securityadcode': 'NA,ND',
        'filingdate.gte': six_months_ago.strftime('%Y-%m-%d'),
        'transactionvalue.gte': 100000,
        'qopts.columns': 'ticker,issuername,filingdate,transactiondate,transactionvalue,securityadcode,transactioncode',
        'api_key': NASDAQ_API_KEY,
    }
    params_3m = params_6m.copy()
    params_3m['filingdate.gte'] = three_months_ago.strftime('%Y-%m-%d')
    params_1m = params_6m.copy()
    params_1m['filingdate.gte'] = one_month_ago.strftime('%Y-%m-%d')
    if tickers_list:
        params_6m['ticker'] = ','.join(tickers_list)
        params_3m['ticker'] = ','.join(tickers_list)
        params_1m['ticker'] = ','.join(tickers_list)

    def url_with_params(base, params):
        from urllib.parse import urlencode
        return f"{base}?{urlencode(params)}"

    print("[Insider Buying] 6M URL:", url_with_params(nasdaq_url, params_6m))
    print("[Insider Buying] 3M URL:", url_with_params(nasdaq_url, params_3m))
    print("[Insider Buying] 1M URL:", url_with_params(nasdaq_url, params_1m))

    # Fetch all pages using cursor-based pagination
    rows = []
    col_idx = {}
    cursor_id = None
    first = True
    while True:
        params = params_6m.copy()
        if not first and cursor_id:
            params['qopts.cursor_id'] = cursor_id
        try:
            resp = requests.get(nasdaq_url, params=params, timeout=30)
            if resp.status_code == 200:
                payload = resp.json()
                page_rows, page_col_idx = parse_datatable(payload)
                if first:
                    col_idx = page_col_idx
                    first = False
                rows.extend(page_rows)
                cursor_id = payload.get('meta', {}).get('next_cursor_id')
                if not cursor_id:
                    break
            else:
                break
        except Exception:
            break

    # Aggregate
    ticker_sums = {}
    def to_date(s):
        if not s:
            return None
        try:
            return datetime.strptime(str(s)[:10], '%Y-%m-%d').date()
        except Exception:
            return None

    for r in rows:
        t = r[col_idx.get('ticker')] if 'ticker' in col_idx else None
        if not t:
            continue
        if tickers_list and t.upper() not in tickers_list:
            continue
        company = r[col_idx.get('issuername')] if 'issuername' in col_idx else None
        filingdate = to_date(r[col_idx.get('filingdate')] if 'filingdate' in col_idx else None)
        transdate = to_date(r[col_idx.get('transactiondate')] if 'transactiondate' in col_idx else None)
        date_used = transdate or filingdate
        if date_used is None:
            continue
        transaction_code = r[col_idx.get('transactioncode')] if 'transactioncode' in col_idx else None
        if transaction_code not in ('P', 'S'):
            continue
        securityadcode = r[col_idx.get('securityadcode')] if 'securityadcode' in col_idx else None
        try:
            val_raw = r[col_idx.get('transactionvalue')] if 'transactionvalue' in col_idx else None
            val = float(val_raw) if val_raw is not None else 0.0
        except Exception:
            val = 0.0
        # Only sum positive values
        if val <= 0:
            continue

        # NA (acquisition): add, ND (disposition): subtract
        if securityadcode == 'ND':
            val = -val

        agg = ticker_sums.setdefault(t, {'ticker': t, 'company': company or '', 'buy6m': 0.0, 'buy3m': 0.0, 'buy1m': 0.0})
        if date_used >= six_months_ago:
            agg['buy6m'] += val
            if date_used >= three_months_ago:
                agg['buy3m'] += val
                if date_used >= one_month_ago:
                    agg['buy1m'] += val

    # Build result list
    result_rows = list(ticker_sums.values())
    result_rows.sort(key=lambda x: x.get('buy6m', 0.0), reverse=True)

    return jsonify({
        'rows': result_rows,
        'meta': {
            'from': six_months_ago.strftime('%Y-%m-%d'),
            'generatedAt': datetime.utcnow().isoformat() + 'Z'
        }
    })

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
