from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
from dotenv import load_dotenv
import json

load_dotenv()

app = Flask(__name__)
CORS(app)

TIINGO_API_KEY = os.getenv("API_KEY")
TIINGO_BASE_URL = 'https://api.tiingo.com'

# In-memory portfolio for demo
portfolio = set()

@app.route('/api/search', methods=['GET'])
def search_ticker():
    query = request.args.get('q', '')
    if not query:
        return jsonify([])
    url = f"{TIINGO_BASE_URL}/tiingo/utilities/search"
    params = {'query': query, 'token': TIINGO_API_KEY}
    resp = requests.get(url, params=params)
    return jsonify(resp.json())

@app.route('/api/portfolio', methods=['GET', 'POST'])
def manage_portfolio():
    if request.method == 'POST':
        data = request.get_json()
        ticker = data.get('ticker')
        if ticker:
            portfolio.add(ticker.upper())
    return jsonify(list(portfolio))

@app.route('/api/ticker/<ticker>/summary', methods=['GET'])
def ticker_summary(ticker):
    # Mock response for development
    mock_data = {
        "prices": [
            {
                "adjClose": 249.34,
                "adjHigh": 251.82,
                "adjLow": 247.47,
                "adjOpen": 249.485,
                "adjVolume": 33893611,
                "close": 249.34,
                "date": "2025-10-15T00:00:00+00:00",
                "divCash": {"source": "0.0", "parsedValue": 0},
                "high": 251.82,
                "low": 247.47,
                "open": 249.485,
                "splitFactor": {"source": "1.0", "parsedValue": 1},
                "volume": 33893611
            }
        ]
    }
    return jsonify(mock_data)

@app.route('/api/ticker/<ticker>/intraday', methods=['GET'])
def ticker_intraday(ticker):
    filename = "mock/intraday.json"
    try:
        with open(filename, 'r') as f:
            mock_data = json.load(f)
        return mock_data
    except FileNotFoundError:
        print(f"Error: The file '{filename}' was not found.")
        return None
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from '{filename}'.")
        return None

# @app.route('/api/ticker/<ticker>/intraday', methods=['GET'])
# def ticker_intraday(ticker):
#     # Fetch intraday prices from Tiingo IEX endpoint, resampled to 4min
#     url = f"{TIINGO_BASE_URL}/iex/{ticker}/prices"
#     params = {'token': TIINGO_API_KEY, 'resampleFreq': '4min'}
#     resp = requests.get(url, params=params)
#     if resp.status_code != 200:
#         return jsonify({'error': 'Failed to fetch intraday prices'}), 500
#     intraday = resp.json()
#     # Fetch previous day's close
#     prev_close_url = f"{TIINGO_BASE_URL}/tiingo/daily/{ticker}/prices"
#     prev_close_params = {'token': TIINGO_API_KEY, 'limit': 2}
#     prev_close_resp = requests.get(prev_close_url, params=prev_close_params)
#     prev_close = None
#     if prev_close_resp.status_code == 200:
#         prices = prev_close_resp.json()
#         if len(prices) > 1:
#             prev_close = prices[1].get('close')
#         elif len(prices) == 1:
#             prev_close = prices[0].get('close')
#     return jsonify({'intraday': intraday, 'prevClose': prev_close})

# @app.route('/api/ticker/<ticker>/summary', methods=['GET'])
# def ticker_summary(ticker):
#     # Prices only
#     price_url = f"{TIINGO_BASE_URL}/tiingo/daily/{ticker}/prices"
#     price_params = {'token': TIINGO_API_KEY}
#     price_resp = requests.get(price_url, params=price_params)
#     return jsonify({
#         'prices': price_resp.json()
#     })

@app.route('/api/ticker/<ticker>/news', methods=['GET'])
def ticker_news(ticker):
    news_url = f"{TIINGO_BASE_URL}/tiingo/news"
    news_params = {'tickers': ticker, 'token': TIINGO_API_KEY}
    news_resp = requests.get(news_url, params=news_params)
    return jsonify(news_resp.json())

@app.route('/api/ticker/<ticker>/financials', methods=['GET'])
def ticker_financials(ticker):
    # Fundamentals
    url = f"{TIINGO_BASE_URL}/tiingo/fundamentals/{ticker}/statements"
    params = {'token': TIINGO_API_KEY, 'statementType': request.args.get('type', 'income'), 'period': request.args.get('period', 'annual'), 'limit': request.args.get('limit', 10)}
    resp = requests.get(url, params=params)
    return jsonify(resp.json())

if __name__ == '__main__':
    app.run(debug=True)
