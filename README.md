# analyst_bot
To build and deploy a financial trend-detection bot

# Easy Customizations
- Add more tickers — just edit the WATCHLIST array in analyst_bot.py
- Change delivery time — edit the cron in the .yml: "30 5 * * 1-5" = 5:30 UTC = 7:30 Paris
- Add a Slack notification — replace send_email() with a Slack webhook call (easy ~5 lines)
- Add weekend crypto — change * * 1-5 to * * * and add tickers like BTC-USD, ETH-USD

# Test it locally
```
pip install -r requirements.txt

python3 analyst_bot.py
```