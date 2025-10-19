# import requests
# import pandas as pd
# import os
# from dotenv import load_dotenv

# load_dotenv()
# API_KEY = os.getenv("API_KEY")
# ticker = "AAPL"
# url = f"https://api.tiingo.com/tiingo/daily/AAPL/prices"

# print(API_KEY)

# headers = {
#     "Content-Type": "application/json",
#     "Authorization": f"Token {API_KEY}"
# }

# params = {
#     "startDate": "2020-01-01",
#     "endDate": "2025-10-14",
#     "resampleFreq": "daily"
# }

# response = requests.get(url, headers=headers, params=params)
# data = response.json()

# df = pd.DataFrame(data)
# df["date"] = pd.to_datetime(df["date"])
# df = df[["date", "open", "high", "low", "close", "volume"]]

# print(df.head())
